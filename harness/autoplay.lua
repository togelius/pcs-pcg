-- PCS autoplay driver: boot -> load a named board -> play it with random
-- (but paced) flipper actions, logging fitness state. MAME 0.264, apple2p.
--
-- This drives the real PCS UI end to end (verified sequence):
--   1. Editor is up ~11s after boot (timings are deterministic per disk).
--   2. Click the DISK icon (toolbar, x=252..279, y=171..184).
--   3. Disk screen: click LOAD (y 75..85), type the board name + Return
--      via the natural keyboard, any key at the "INSERT GAME DISK" prompt.
--   4. Back at the disk screen: click PLAY GAME (y 147..167).
--   5. Game engine: wait through the attract delay, press+release the
--      button at player select, then plunger-launch and work the flippers.
--
-- Input facts (measured):
--   * Buttons: set_value(1) = pressed ($C061 bit7 high), 0 = released.
--   * Cursor: poke $82 (Y), $83 (X div 7), $84 (X mod 7); with the
--     joystick centered (128) the poked position is stable.
--   * Clicks dispatch on RELEASE while the cursor is in the item rect.
--   * Plunger: hold button 0 with paddle 1 setting the spring, release
--     to fire. Button 0 is also the left flipper.
--
-- Environment:
--   PCS_BOARDNAME  catalog name to LOAD, without .PB (default "DEMO1")
--   PCS_FRAMES     frames to run after play starts (default 6000)
--   PCS_LOG        CSV log path (optional)
--   PCS_SNAP       final screenshot path (optional)
--   PCS_SEED       RNG seed for the action policy (default 1)

local mach = manager.machine
local mem  = mach.devices[":maincpu"].spaces["program"]
local B  = mach.ioport.ports[":gameio:joy:joystick_buttons"]
local b1 = B.fields["P1 Button 1"]
local b2 = B.fields["P1 Button 2"]
local jx = mach.ioport.ports[":gameio:joy:joystick_1_x"].fields["P1 Joystick X"]
local jy = mach.ioport.ports[":gameio:joy:joystick_1_y"].fields["P1 Joystick Y"]
local nat = mach.natkeyboard

local PRESS, REL = 1, 0
local boardname = os.getenv("PCS_BOARDNAME") or "DEMO1"
local play_frames = tonumber(os.getenv("PCS_FRAMES")) or 6000
local logp = os.getenv("PCS_LOG")
local snap = os.getenv("PCS_SNAP")
math.randomseed(tonumber(os.getenv("PCS_SEED")) or 1)

local function s8(v) return v < 128 and v or v - 256 end
local function cur(d7, m7, y)
  mem:write_u8(0x83, d7); mem:write_u8(0x84, m7); mem:write_u8(0x82, y)
end

-- Score: the engine keeps the player-1 score as 8 decimal digit bytes
-- (MSD first) at $8952 (located empirically by diffing RAM dumps during a
-- scoring game; the shipped binary differs from the GitHub source, so the
-- source labels couldn't be used). The score resets when the game ends and
-- the attract loop restarts, so track the maximum seen.
local SCORE1 = 0x8952
local max_score = 0
local function read_score()
  local v = 0
  for i = 0, 7 do
    local d = mem:read_u8(SCORE1 + i)
    if d > 9 then return nil end   -- not in game mode yet
    v = v * 10 + d
  end
  return v
end

local log = logp and io.open(logp, "w") or nil
if log then log:write("frame,score,ballx,bally,balldx,balldy,bst\n") end

-- flipper policy state: independent paced presses instead of per-frame coin
-- flips, so flips last long enough to matter.
local hold1, hold2 = 0, 0
-- plunger cycle: periodically pull and release with random strength.
local plunger = 0
local play_t0 = nil
local frame = 0

emu.register_frame_done(function()
  frame = frame + 1
  jx:set_value(128); jy:set_value(128)
  b1:set_value(REL); b2:set_value(REL)

  -- ---- scripted UI phase (deterministic boot timings) ----
  if frame >= 820 and frame < 920 then cur(0x24, 0, 177) end       -- DISK icon
  if frame >= 840 and frame < 880 then b1:set_value(PRESS) end
  if frame >= 1100 and frame < 1200 then cur(24, 0, 80) end        -- LOAD
  if frame >= 1120 and frame < 1160 then b1:set_value(PRESS) end
  if frame == 1300 then nat:post(boardname .. "\n") end            -- name
  if frame == 1700 then nat:post(" ") end                          -- any key
  if frame >= 2150 and frame < 2250 then cur(24, 3, 157) end       -- PLAY GAME
  if frame >= 2170 and frame < 2210 then b1:set_value(PRESS) end
  if frame >= 2950 and frame < 2980 then b1:set_value(PRESS) end   -- 1 player

  -- ---- play phase ----
  if frame >= 3000 then
    if not play_t0 then play_t0 = frame end
    local t = frame - play_t0

    -- plunger cycle every ~10s: pull with random strength, release.
    if plunger == 0 and t % 600 == 50 then
      plunger = 60 + math.random(40)
    end
    if plunger > 0 then
      plunger = plunger - 1
      jy:set_value(120 + math.random(135))   -- spring strength
      b1:set_value(PRESS)
    else
      -- paced random flippers
      if hold1 == 0 and math.random() < 0.04 then hold1 = 8 + math.random(15) end
      if hold2 == 0 and math.random() < 0.04 then hold2 = 8 + math.random(15) end
      if hold1 > 0 then hold1 = hold1 - 1; b1:set_value(PRESS) end
      if hold2 > 0 then hold2 = hold2 - 1; b2:set_value(PRESS) end
    end

    local sc = read_score()
    if sc and sc > max_score then max_score = sc end
    if log then
      log:write(string.format("%d,%d,%d,%d,%d,%d,%d\n",
        t, sc or 0, mem:read_u8(0xD2), mem:read_u8(0xD3),
        s8(mem:read_u8(0xD6)), s8(mem:read_u8(0xD7)), mem:read_u8(0xD1)))
    end
    if t >= play_frames then
      if snap then local s = mach.screens[":screen"]; if s then s:snapshot(snap) end end
      if log then log:flush(); log:close() end
      emu.print_info(string.format("[autoplay] done: frames=%d max_score=%d", t, max_score))
      mach:exit()
    end
  end
end)

emu.print_info("[autoplay] driver attached, board=" .. boardname)
