-- File-IPC Gym bridge for PCS pinball in MAME.
--
-- This MAME build ships no socket library, so the bridge speaks a tiny
-- file protocol on a tmpfs directory (PCS_IPC_DIR). The Python side is
-- tools/pcs_env.py. One bridge instance serves one environment.
--
-- Protocol (newline-free single-line files, sequence-numbered):
--   Python writes  <dir>/cmd : "<seq> reset <plunger 0..255>"
--                              "<seq> step <action 0..3>"
--                              "<seq> quit"
--   Lua    writes  <dir>/rsp : "<seq> <payload>"  (atomic via rename)
--
--   reset -> restores the save state (player select), starts a 1-player
--            game, waits for the ball to settle on the spring (= drain
--            anchor), fires the plunger at the given strength, re-plunging
--            up to 5x stronger if the ball falls back without entering
--            play. Replies "ok <x> <y> <dx> <dy> <score> <inplay>";
--            inplay=0 marks a launch-degenerate board.
--   step  -> applies the action (0=none 1=left 2=right 3=both) for
--            PCS_SKIP frames, then (gating) keeps emulating with no-op
--            while the ball is above the actionable region, up to
--            PCS_SKIP_CAP frames. Replies
--            "<x> <y> <dx> <dy> <score> <done> <frames>"
--            done=1 when the ball drained (re-served into the lane).
--
-- While idle the bridge BLOCKS inside the frame callback polling for the
-- next command: the emulator advances zero frames between steps, which
-- makes episodes deterministic for a fixed plunger strength + action
-- sequence (verified bit-exact).
--
-- Environment:
--   PCS_IPC_DIR   IPC directory (required)
--   PCS_STATE     save state file to restore on reset (required)
--   PCS_SKIP      frames per action (default 10, i.e. 6 actions/s)
--   PCS_ACT_Y     ball y >= this counts as actionable (default 100)
--   PCS_SKIP_CAP  max auto-skip frames per step (default 300)

local mach = manager.machine
local mem  = mach.devices[":maincpu"].spaces["program"]
local B  = mach.ioport.ports[":gameio:joy:joystick_buttons"]
local b1 = B.fields["P1 Button 1"]
local b2 = B.fields["P1 Button 2"]
local jx = mach.ioport.ports[":gameio:joy:joystick_1_x"].fields["P1 Joystick X"]
local jy = mach.ioport.ports[":gameio:joy:joystick_1_y"].fields["P1 Joystick Y"]

local PRESS, REL = 1, 0
local DIR   = assert(os.getenv("PCS_IPC_DIR"), "PCS_IPC_DIR required")
local STATE = assert(os.getenv("PCS_STATE"), "PCS_STATE required")
local SKIP  = tonumber(os.getenv("PCS_SKIP")) or 10
local ACT_Y = tonumber(os.getenv("PCS_ACT_Y")) or 100
local CAP   = tonumber(os.getenv("PCS_SKIP_CAP")) or 300

local SCORE1 = 0x8952
local function s8(v) return v < 128 and v or v - 256 end

local function read_ball()
  return mem:read_u8(0xD2), mem:read_u8(0xD3),
         s8(mem:read_u8(0xD6)), s8(mem:read_u8(0xD7))
end

local last_score = 0
local function read_score()
  local v = 0
  for i = 0, 7 do
    local d = mem:read_u8(SCORE1 + i)
    if d > 9 then return last_score end  -- transient/not in game
    v = v * 10 + d
  end
  last_score = v
  return v
end

local last_seq = nil
local function poll_cmd()
  local f = io.open(DIR .. "/cmd", "r")
  if not f then return nil end
  local line = f:read("*l"); f:close()
  if not line then return nil end
  local seq, rest = line:match("^(%d+) (.+)$")
  if not seq or seq == last_seq then return nil end
  last_seq = seq
  return seq, rest
end

local function respond(seq, payload)
  local f = assert(io.open(DIR .. "/rsp.tmp", "w"))
  f:write(seq .. " " .. payload); f:close()
  os.remove(DIR .. "/rsp")
  os.rename(DIR .. "/rsp.tmp", DIR .. "/rsp")
end

-- ---- per-frame state machine ------------------------------------------
-- mode: "idle" | "reset" | "step"
local mode = "idle"
local seq = nil
local t = 0                  -- frames into current mode
local plunger = 255
local action = 0
local start_x, start_y = nil, nil  -- ball resting position (drain anchor)
local been_away = false            -- ball has left the anchor area
local last_x, last_y = -1, -1      -- settle detector state
local stable = 0
local t_plunge = 0
local attempts = 0
local game_started = false
local t_started = 0
local step_frames = 0
local bridge_frame = 0

local function set_action(a)
  b1:set_value((a == 1 or a == 3) and PRESS or REL)
  b2:set_value((a == 2 or a == 3) and PRESS or REL)
end

-- Drain detector. The anchor (start_x, start_y) is the ball's resting
-- point on the launcher spring, captured at reset. "In play" = the ball
-- has left the lane column (x deviates from the anchor by > 12). A
-- drained ball is re-served at the top of the lane and falls back to
-- the spring, so: drain = in play, then ball reappears in the lane
-- bottom (|x - anchor_x| <= 2 and y >= anchor_y - 10). The x-gate keeps
-- outlane passes (outside the lane wall) from false-positiving.
local in_play = false
local function note_position(x, y)
  if start_x and math.abs(x - start_x) > 12 then in_play = true end
end
local function check_drain(x, y)
  if not start_x or not in_play then return false end
  return math.abs(x - start_x) <= 2 and y >= start_y - 10
end

emu.register_frame_done(function()
  bridge_frame = bridge_frame + 1
  jx:set_value(128); jy:set_value(128)
  b1:set_value(REL); b2:set_value(REL)

  if mode == "idle" then
    -- Block inside the frame callback until the next command arrives:
    -- the emulator advances zero frames while Python thinks, which keeps
    -- episodes deterministic and loses no events. Give up after 60s
    -- (Python died) so the process can't hang forever.
    local s, cmd
    local deadline = os.time() + 600
    repeat
      s, cmd = poll_cmd()
      if not s and os.time() > deadline then
        emu.print_info("[bridge] no commands for 600s, exiting")
        mach:exit()
        return
      end
    until s
    seq = s
    if cmd:match("^quit") then
      respond(seq, "bye")
      mach:exit()
    elseif cmd:match("^reset") then
      plunger = tonumber(cmd:match("^reset (%d+)")) or 255
      mach:load(STATE)
      mode = "reset"; t = 0; last_score = 0
      start_x, start_y, been_away = nil, nil, false
      last_x, last_y, stable, t_plunge = -1, -1, 0, 0
      attempts = 0; in_play = false
      game_started = false; t_started = 0
    elseif cmd:match("^step") then
      action = tonumber(cmd:match("^step (%d+)")) or 0
      mode = "step"; t = 0; step_frames = 0
    elseif cmd:match("^peek") then
      local addr = tonumber(cmd:match("^peek (%d+)"))
      respond(seq, tostring(mem:read_u8(addr)))
    elseif cmd:match("^anchor") then
      respond(seq, string.format("%s %s %s", tostring(start_x),
        tostring(start_y), tostring(been_away)))
    end

  elseif mode == "reset" then
    t = t + 1
    -- Phase 1 (t 1..10): let the loaded state settle.
    -- Phase 2: tap the button (3 on / 27 off) to get through player
    --   select. Game start is detected as the ball MOVING: the served
    --   ball materialises and falls down the launcher lane, giving
    --   several consecutive frames of changing, nonzero coordinates.
    --   (The source's $82 ball counter doesn't exist in the shipped
    --   binary, and the score digits are already zeroed at the select
    --   screen, so motion is the reliable signal.)
    -- Phase 3: stop touching the button; wait for the ball to settle on
    --   the spring (12 near-still frames) -- that resting point is the
    --   drain anchor. Then hold the plunger 90 frames at the requested
    --   strength, release, wait 60 frames, reply.
    local x, y = read_ball()
    if not game_started then
      if t > 10 then
        if (t - 10) % 30 < 3 then b1:set_value(PRESS) end
        local moving = x ~= 0 and y ~= 0 and last_x >= 0
                       and (x ~= last_x or y ~= last_y)
        stable = moving and stable + 1 or 0
        if stable >= 3 then
          game_started = true; stable = 0
        elseif t > 900 then
          respond(seq, "err reset-no-game-start")
          mode = "idle"
        end
      end
      last_x, last_y = x, y
    elseif not start_x then
      if x ~= 0 and y ~= 0 and math.abs(x - last_x) <= 1
         and math.abs(y - last_y) <= 1 then
        stable = stable + 1
      else
        stable = 0
      end
      last_x, last_y = x, y
      if stable >= 12 then
        start_x, start_y = x, y
        been_away = false
        t_plunge = t
      elseif t > 1200 then
        respond(seq, "err reset-no-settle")
        mode = "idle"
      end
    elseif t <= t_plunge + 90 then
      jy:set_value(plunger); b1:set_value(PRESS)
      note_position(x, y)
    else
      -- post-launch: wait until the ball either enters play (left the
      -- lane column) or falls back onto the spring (failed launch).
      note_position(x, y)
      if in_play then
        local _, _, dx, dy = read_ball()
        respond(seq, string.format("ok %d %d %d %d %d 1", x, y, dx, dy, read_score()))
        mode = "idle"
      elseif t >= t_plunge + 420 then
        if attempts < 5 then
          -- failed launch (ball never left the lane): re-plunge stronger
          attempts = attempts + 1
          plunger = math.min(255, plunger + 25)
          t_plunge = t
        else
          -- launch-degenerate board; report so Python doesn't hang
          local _, _, dx, dy = read_ball()
          respond(seq, string.format("ok %d %d %d %d %d 0", x, y, dx, dy, read_score()))
          mode = "idle"
        end
      end
    end

  elseif mode == "step" then
    t = t + 1
    step_frames = step_frames + 1
    local x, y, dx, dy = read_ball()
    note_position(x, y)
    local done = check_drain(x, y)

    if t <= SKIP then
      set_action(action)
      if t == SKIP and not done then
        -- gating: if ball is actionable (or we're capped), reply now;
        -- otherwise fall through to auto-skip below
        if y >= ACT_Y then
          respond(seq, string.format("%d %d %d %d %d 0 %d", x, y, dx, dy, read_score(), step_frames))
          mode = "idle"
        end
      end
    else
      -- auto-skip with no-op until actionable, drained, or capped
      if y >= ACT_Y or step_frames >= SKIP + CAP then
        respond(seq, string.format("%d %d %d %d %d 0 %d", x, y, dx, dy, read_score(), step_frames))
        mode = "idle"
      end
    end

    if done and mode == "step" then
      respond(seq, string.format("%d %d %d %d %d 1 %d", x, y, dx, dy, read_score(), step_frames))
      mode = "idle"
    end
  end
end)

-- signal readiness
local f = assert(io.open(DIR .. "/ready.tmp", "w"))
f:write("1"); f:close()
os.rename(DIR .. "/ready.tmp", DIR .. "/ready")
emu.print_info("[bridge] listening on " .. DIR)
