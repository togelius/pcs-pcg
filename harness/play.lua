-- PCS play/eval driver for MAME (apple2p), built on harness/pcs.lua.
--
-- Implements the verified low-level input primitives for driving PCS and a
-- configurable action timeline, then logs fitness state and screenshots.
--
-- Verified facts this is built on (see docs/AUTOMATION.md):
--   * Cursor position is held in zero page: CURSORY=$82, CURSORXDIV7=$83,
--     CURSORXMOD7=$84. With the emulated paddles centered (joystick=128)
--     the cursor receives zero drift, so poking these bytes positions the
--     pointer deterministically.
--   * The game-I/O button polarity is INVERTED relative to set_value:
--     field:set_value(0) drives $C061/$C062 bit7 high == "pressed",
--     set_value(1) == "released".
--   * Toolbar boxes (EDIT.S): PLAY icon PLAYB is at xdiv7=$24, y 115..127;
--     DISK icon DISKB at xdiv7=$24, y 171..184; the whole toolbar TOOLB is
--     xdiv7=$24, full height.
--
-- Configure via environment:
--   PCS_LOG    CSV state log path (optional)
--   PCS_FRAMES total frames to run before exit (default 1800)
--   PCS_SNAP   screenshot path written at exit (optional)
--   PCS_SNAP_EVERY  also snapshot every N frames to PCS_SNAP.<frame>.png
--
-- Run: scripts/run_mame.sh -flop1 DISK.dsk -gameio joy \
--        -video none -sound none -nothrottle \
--        -autoboot_delay 1 -autoboot_script harness/play.lua

local mach = manager.machine
local mem  = mach.devices[":maincpu"].spaces["program"]

-- ---- input primitives --------------------------------------------------
local btnport = mach.ioport.ports[":gameio:joy:joystick_buttons"]
local BTN1 = btnport.fields["P1 Button 1"]   -- flipper L / plunger
local BTN2 = btnport.fields["P1 Button 2"]   -- flipper R
local JX = mach.ioport.ports[":gameio:joy:joystick_1_x"].fields["P1 Joystick X"]
local JY = mach.ioport.ports[":gameio:joy:joystick_1_y"].fields["P1 Joystick Y"]

local PRESSED, RELEASED = 0, 1   -- inverted polarity (see header)

local CURSORY, CURSORXDIV7, CURSORXMOD7 = 0x82, 0x83, 0x84

local input = {}
function input.center_stick() JX:set_value(128); JY:set_value(128) end
function input.button(field, down) field:set_value(down and PRESSED or RELEASED) end
function input.set_cursor(xdiv7, xmod7, y)
  mem:write_u8(CURSORXDIV7, xdiv7)
  mem:write_u8(CURSORXMOD7, xmod7)
  mem:write_u8(CURSORY, y)
end
-- Toolbar icon centers (xdiv7, y).
input.ICON = {
  PLAY = {0x24, 120},
  DISK = {0x24, 177},
}

-- ---- fitness reading ---------------------------------------------------
-- Standalone-game (RUN.S) map; see docs/MEMORY_MAP.md. Used when playing a
-- MADE game. For editor test-play the live addresses differ (open item).
local A = {DSCORE=0xCB, BST=0xD1, X1=0xD2, Y1=0xD3, X2=0xD4, Y2=0xD5,
          BDX=0xD6, BDY=0xD7, BMULT=0xCD, INITMODE=0xCE}
local function s8(v) return v < 128 and v or v - 256 end

local score = 0
local last_dscore = 0
mem:install_write_tap(A.DSCORE, A.DSCORE, "dscore", function(_, data)
  if data == 0 and last_dscore > 0 then score = score + last_dscore end
  last_dscore = data
  return data
end)

-- ---- action timeline ---------------------------------------------------
-- A simple frame-keyed script. Edit/extend for different drive sequences.
-- This default sequence triggers the editor PLAY on the currently loaded
-- board and then works the flippers; load-a-named-board is documented in
-- docs/AUTOMATION.md and can be prepended here.
local function drive(frame)
  input.center_stick()
  input.button(BTN1, false); input.button(BTN2, false)

  -- click PLAY: hold cursor on the icon and press, then release to select
  if frame >= 240 and frame < 360 then
    input.set_cursor(input.ICON.PLAY[1], 0, input.ICON.PLAY[2])
  end
  if frame >= 300 and frame < 345 then input.button(BTN1, true) end

  -- once in play: pulse plunger then alternate flippers
  if frame >= 420 and frame < 520 then input.button(BTN1, true) end
  if frame >= 540 then
    input.button(BTN1, (frame % 30) < 15)
    input.button(BTN2, (frame % 30) >= 15)
  end
end

-- ---- lifecycle ---------------------------------------------------------
local frame = 0
local total = tonumber(os.getenv("PCS_FRAMES")) or 1800
local snap = os.getenv("PCS_SNAP")
local snap_every = tonumber(os.getenv("PCS_SNAP_EVERY"))
local logp = os.getenv("PCS_LOG")
local log = logp and io.open(logp, "w") or nil
if log then log:write("frame,score,ballx,bally,balldx,balldy,bst,bmult,initmode\n") end

local function shoot(path) local s = mach.screens[":screen"]; if s then s:snapshot(path) end end

emu.register_frame_done(function()
  frame = frame + 1
  drive(frame)
  if log then
    local bx = (mem:read_u8(A.X1) + mem:read_u8(A.X2)) // 2
    local by = (mem:read_u8(A.Y1) + mem:read_u8(A.Y2)) // 2
    log:write(string.format("%d,%d,%d,%d,%d,%d,%d,%d,%d\n", frame, score, bx, by,
      s8(mem:read_u8(A.BDX)), s8(mem:read_u8(A.BDY)),
      mem:read_u8(A.BST), mem:read_u8(A.BMULT), mem:read_u8(A.INITMODE)))
  end
  if snap and snap_every and frame % snap_every == 0 then
    shoot(string.format("%s.%05d.png", snap, frame))
  end
  if frame >= total then
    if snap then shoot(snap) end
    if log then log:flush(); log:close() end
    emu.print_info(string.format("[pcs] play done: frames=%d score=%d", frame, score))
    mach:exit()
  end
end)

emu.print_info("[pcs] play harness attached")
