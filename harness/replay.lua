-- Policy replay harness: play N balls on a board with a fixed linear
-- policy (or random), entirely in-Lua, suitable for video capture
-- (-aviwrite). Reuses the bridge's reset/launch/drain logic but drives
-- the flippers itself from a 4x7 weight matrix loaded from PCS_POLICY.
--
-- Environment:
--   PCS_STATE     save state to restore each ball (required)
--   PCS_POLICY    text file: 28 whitespace-separated floats (4 actions x
--                 7 features, row-major). Omit for a random policy.
--   PCS_BALLS     number of balls to play (default 3)
--   PCS_SKIP      frames per action (default 10)
--   PCS_PLUNGER   plunger strength 0..255 (default 220)
--
-- Features phi(x,y,dx,dy) = [1, x/160, y/192, dx/16, dy/16,
--                            (x/160)(dx/16), (y/192)(dy/16)]
-- Action: argmax (greedy) by default; set PCS_TEMP=1 for softmax
-- sampling (matches how policies are evaluated during training --
-- greedy deployment of a softmax-trained policy can collapse).

local mach = manager.machine
local mem  = mach.devices[":maincpu"].spaces["program"]
local b1 = mach.ioport.ports[":gameio:joy:joystick_buttons"].fields["P1 Button 1"]
local b2 = mach.ioport.ports[":gameio:joy:joystick_buttons"].fields["P1 Button 2"]
local jx = mach.ioport.ports[":gameio:joy:joystick_1_x"].fields["P1 Joystick X"]
local jy = mach.ioport.ports[":gameio:joy:joystick_1_y"].fields["P1 Joystick Y"]

local PRESS, REL = 1, 0
local STATE   = assert(os.getenv("PCS_STATE"))
local SKIP    = tonumber(os.getenv("PCS_SKIP")) or 10
local PLUNGER = tonumber(os.getenv("PCS_PLUNGER")) or 220
local NBALLS  = tonumber(os.getenv("PCS_BALLS")) or 3
local TEMP    = tonumber(os.getenv("PCS_TEMP")) or 0
local SCORE1  = 0x8952

-- load policy
local W = nil
local pf = os.getenv("PCS_POLICY")
if pf then
  local f = io.open(pf, "r")
  if f then
    local nums = {}
    for tok in f:read("*a"):gmatch("%S+") do nums[#nums + 1] = tonumber(tok) end
    f:close()
    if #nums >= 28 then
      W = {}
      for a = 0, 3 do
        W[a] = {}
        for k = 1, 7 do W[a][k] = nums[a * 7 + k] end
      end
    end
  end
end

local function s8(v) return v < 128 and v or v - 256 end
local function read_ball()
  return mem:read_u8(0xD2), mem:read_u8(0xD3), s8(mem:read_u8(0xD6)), s8(mem:read_u8(0xD7))
end

local function policy_action(x, y, dx, dy)
  if not W then return math.random(0, 3) end
  local phi = {1.0, x / 160.0, y / 192.0, dx / 16.0, dy / 16.0,
               (x / 160.0) * (dx / 16.0), (y / 192.0) * (dy / 16.0)}
  local z = {}
  local zmax = -1e9
  for a = 0, 3 do
    z[a] = 0
    for k = 1, 7 do z[a] = z[a] + W[a][k] * phi[k] end
    if z[a] > zmax then zmax = z[a] end
  end
  if TEMP > 0 then
    local p, tot = {}, 0
    for a = 0, 3 do p[a] = math.exp((z[a] - zmax) / TEMP); tot = tot + p[a] end
    local r = math.random() * tot
    for a = 0, 3 do
      r = r - p[a]
      if r <= 0 then return a end
    end
    return 3
  end
  local best, ba = -1e9, 0
  for a = 0, 3 do
    if z[a] > best then best = z[a]; ba = a end
  end
  return ba
end

local function set_action(a)
  b1:set_value((a == 1 or a == 3) and PRESS or REL)
  b2:set_value((a == 2 or a == 3) and PRESS or REL)
end

-- state machine: mirror the bridge's reset, then self-drive
local phase, t, ball = "boot", 0, 0
local boot_frames = 0
local start_x, start_y, in_play = nil, nil, false
local last_x, last_y, stable, t_plunge, game_started, t_started = -1, -1, 0, 0, false, 0
local skip_ctr, cur_action, play_t = 0, 0, 0

local function begin_reset()
  mach:load(STATE)
  phase, t = "reset", 0
  start_x, start_y, in_play = nil, nil, false
  last_x, last_y, stable, t_plunge = -1, -1, 0, 0
  game_started, t_started = false, false
end

emu.register_frame_done(function()
  jx:set_value(128); jy:set_value(128); b1:set_value(REL); b2:set_value(REL)

  if phase == "boot" then
    -- let MAME fully boot the disk before restoring a save state;
    -- loading at frame 1 yields a corrupt restore
    boot_frames = boot_frames + 1
    if boot_frames >= 240 then begin_reset() end

  elseif phase == "reset" then
    t = t + 1
    local x, y = read_ball()
    if not game_started then
      if t > 10 then
        if (t - 10) % 30 < 3 then b1:set_value(PRESS) end
        if x ~= 0 and y ~= 0 and last_x >= 0 and (x ~= last_x or y ~= last_y) then
          stable = stable + 1 else stable = 0 end
        if stable >= 3 then game_started = true; stable = 0 end
      end
      last_x, last_y = x, y
    elseif not start_x then
      if x ~= 0 and y ~= 0 and math.abs(x - last_x) <= 1 and math.abs(y - last_y) <= 1 then
        stable = stable + 1 else stable = 0 end
      last_x, last_y = x, y
      if stable >= 12 then start_x, start_y = x, y; t_plunge = t end
    elseif t <= t_plunge + 90 then
      jy:set_value(PLUNGER); b1:set_value(PRESS)
      if start_x and math.abs(x - start_x) > 12 then in_play = true end
    else
      phase, skip_ctr, cur_action, play_t = "play", 0, 0, 0
    end

  elseif phase == "play" then
    play_t = play_t + 1
    local x, y, dx, dy = read_ball()
    if start_x and math.abs(x - start_x) > 12 then in_play = true end
    -- drain check (only after the ball has actually entered play and a
    -- minimum dwell, so a settling frame can't end the ball instantly)
    if in_play and play_t > 20
       and math.abs(x - start_x) <= 2 and y >= start_y - 10 then
      ball = ball + 1
      if ball >= NBALLS then mach:exit() else begin_reset() end
      return
    end
    if skip_ctr <= 0 then
      cur_action = policy_action(x, y, dx, dy)
      skip_ctr = SKIP
    end
    set_action(cur_action)
    skip_ctr = skip_ctr - 1
  end
end)

emu.print_info("[replay] policy=" .. (W and "linear" or "random")
               .. " balls=" .. NBALLS)
