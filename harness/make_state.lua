-- Create a per-board save state at the player-select screen.
--
-- Drives the verified UI sequence (DISK icon -> LOAD -> board name ->
-- PLAY GAME) and saves the machine state just before player select, i.e.
-- after INITWORLD has baked the board into the engine. Restoring this
-- state and pressing the button gives a fresh 5-ball game on the board
-- in ~1 second of emulated time, skipping the ~50s boot/UI sequence.
--
-- Environment:
--   PCS_BOARDNAME  catalog name to LOAD (default "EVOLVED")
--   PCS_STATE      output state file path (required)
--   PCS_SNAP       optional screenshot path saved just before the state

local mach = manager.machine
local mem  = mach.devices[":maincpu"].spaces["program"]
local b1 = mach.ioport.ports[":gameio:joy:joystick_buttons"].fields["P1 Button 1"]
local jx = mach.ioport.ports[":gameio:joy:joystick_1_x"].fields["P1 Joystick X"]
local jy = mach.ioport.ports[":gameio:joy:joystick_1_y"].fields["P1 Joystick Y"]
local nat = mach.natkeyboard

local PRESS, REL = 1, 0
local boardname = os.getenv("PCS_BOARDNAME") or "EVOLVED"
local statefile = assert(os.getenv("PCS_STATE"), "PCS_STATE required")
local snap = os.getenv("PCS_SNAP")
local frame = 0

local function cur(d7, m7, y)
  mem:write_u8(0x83, d7); mem:write_u8(0x84, m7); mem:write_u8(0x82, y)
end

emu.register_frame_done(function()
  frame = frame + 1
  jx:set_value(128); jy:set_value(128); b1:set_value(REL)

  if frame >= 820 and frame < 920 then cur(0x24, 0, 177) end       -- DISK icon
  if frame >= 840 and frame < 880 then b1:set_value(PRESS) end
  if frame >= 1100 and frame < 1200 then cur(24, 0, 80) end        -- LOAD
  if frame >= 1120 and frame < 1160 then b1:set_value(PRESS) end
  if frame == 1300 then nat:post(boardname .. "\n") end
  if frame == 1700 then nat:post(" ") end                          -- insert prompt
  if frame >= 2150 and frame < 2250 then cur(24, 3, 157) end       -- PLAY GAME
  if frame >= 2170 and frame < 2210 then b1:set_value(PRESS) end

  -- by ~2900 the engine sits in GETPLAYERCNT waiting for a button
  if frame == 2900 then
    if snap then local s = mach.screens[":screen"]; if s then s:snapshot(snap) end end
    mach:save(statefile)
    emu.print_info("[make_state] state save scheduled -> " .. statefile)
  end
  if frame == 2960 then
    local f = io.open(statefile, "rb")
    emu.print_info("[make_state] state file written: " .. tostring(f ~= nil))
    if f then f:close() end
    mach:exit()
  end
end)

emu.print_info("[make_state] driver attached, board=" .. boardname)
