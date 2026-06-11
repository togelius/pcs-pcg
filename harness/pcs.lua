-- PCS evaluation harness for MAME (apple2p), MAME 0.264 Lua API.
--
-- Provides:
--   * memory read helpers over the 6502 program space
--   * the PCS runtime state struct (ball, score-delta, flags) read from
--     the zero-page map documented in docs/MEMORY_MAP.md
--   * a robust cumulative-score accumulator using a write tap on DSCORE,
--     since the game zeroes DSCORE every frame after flushing it
--   * frame hooks, screenshots, and an exit-after-N-frames helper
--
-- Configure via environment variables (read with os.getenv):
--   PCS_LOG       path to write a CSV state log (optional)
--   PCS_FRAMES    stop after this many emulated frames (optional)
--   PCS_SNAP      path to write a final screenshot (optional)
--
-- Run with:
--   scripts/run_mame.sh -flop1 disks/pcs.dsk \
--       -video none -sound none -nothrottle \
--       -autoboot_delay 1 -autoboot_script harness/pcs.lua

local M = {}

local machine = manager.machine
local mem = machine.devices[":maincpu"].spaces["program"]

-- ---- zero-page addresses (see docs/MEMORY_MAP.md) ----------------------
M.addr = {
  DSCORE   = 0xCB,  -- per-flush score delta (saturates 0..255, then zeroed)
  DBONUS   = 0xCC,  -- bonus delta
  BMULT    = 0xCD,  -- bonus multiplier
  INITMODE = 0xCE,  -- nonzero while a ball is being launched/initialised
  BST      = 0xD1,  -- ball status / state byte
  X1       = 0xD2,  -- ball bounding box (hi-res coords)
  Y1       = 0xD3,
  X2       = 0xD4,
  Y2       = 0xD5,
  BDX      = 0xD6,  -- ball velocity X (signed)
  BDY      = 0xD7,  -- ball velocity Y (signed)
  PB_COUNT = 0x401C, -- PBDATA: object count of the loaded board
}

local function s8(v) return v < 128 and v or v - 256 end

function M.read(name) return mem:read_u8(M.addr[name]) end

-- Ball center, derived from the bounding box.
function M.ball()
  local x1, y1 = mem:read_u8(M.addr.X1), mem:read_u8(M.addr.Y1)
  local x2, y2 = mem:read_u8(M.addr.X2), mem:read_u8(M.addr.Y2)
  return {
    x = (x1 + x2) // 2, y = (y1 + y2) // 2,
    dx = s8(mem:read_u8(M.addr.BDX)), dy = s8(mem:read_u8(M.addr.BDY)),
  }
end

-- ---- cumulative score via write tap -----------------------------------
-- DOSCORE flushes DSCORE into the multi-digit player score then zeroes it,
-- so a poller can miss values. Tapping writes to DSCORE and summing the
-- value seen *just before* it returns to 0 captures every point reliably.
M.score = 0
local last_dscore = 0
local function on_dscore_write(offset, data)
  if data == 0 and last_dscore > 0 then
    M.score = M.score + last_dscore
  end
  last_dscore = data
  return data
end

function M.install_score_tap()
  mem:install_write_tap(M.addr.DSCORE, M.addr.DSCORE, "dscore", on_dscore_write)
end

-- ---- logging / lifecycle ----------------------------------------------
local log_file, frame_limit, snap_path
local frame = 0

local function snapshot(path)
  local scr = machine.screens[":screen"]
  if scr then scr:snapshot(path) end
end

local function on_frame()
  frame = frame + 1
  if log_file then
    local b = M.ball()
    log_file:write(string.format("%d,%d,%d,%d,%d,%d,%d,%d,%d\n",
      frame, M.score, b.x, b.y, b.dx, b.dy,
      M.read("BST"), M.read("BMULT"), M.read("INITMODE")))
  end
  if frame_limit and frame >= frame_limit then
    if snap_path then snapshot(snap_path) end
    if log_file then log_file:flush(); log_file:close() end
    emu.print_info(string.format("[pcs] done: frames=%d score=%d", frame, M.score))
    machine:exit()
  end
end

function M.start()
  M.install_score_tap()
  local logp = os.getenv("PCS_LOG")
  if logp then
    log_file = io.open(logp, "w")
    log_file:write("frame,score,ballx,bally,balldx,balldy,bst,bmult,initmode\n")
  end
  local fr = os.getenv("PCS_FRAMES")
  frame_limit = fr and tonumber(fr) or nil
  snap_path = os.getenv("PCS_SNAP")
  emu.register_frame_done(on_frame)
  emu.print_info("[pcs] harness attached")
end

M.start()
return M
