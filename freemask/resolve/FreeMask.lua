--[[
  FreeMask for DaVinci Resolve — works in the free version.

  Workspace > Scripts > FreeMask
    1. Put the playhead over the clip you want to mask (Edit or Cut page).
    2. Run this script. FreeMask opens with that clip, at the playhead frame.
    3. Click what you want, Track, Export, then "Finish & send to Resolve".
    4. The mattes land in a "FreeMask" bin, and the cutout is placed on a new
       video track right above your clip, frame-accurate.

  The installer replaces __FREEMASK_HOME__ with the folder FreeMask lives in.
]]

local FREEMASK_HOME = [[__FREEMASK_HOME__]]

local STARTUP_TIMEOUT = FREEMASK_STARTUP_TIMEOUT or 180   -- seconds for the app to start
local HEARTBEAT_TIMEOUT = FREEMASK_HEARTBEAT_TIMEOUT or 60 -- seconds without a heartbeat

local SEP = package.config:sub(1, 1)
local IS_WIN = SEP == "\\"

local function log(msg)
  print("[FreeMask] " .. tostring(msg))
end

local function join(...)
  return table.concat({ ... }, SEP)
end

local function exists(path)
  local f = io.open(path, "r")
  if f then f:close() return true end
  return false
end

local function dirname(path)
  return path:match("^(.*)[/\\][^/\\]*$") or "."
end

local function basename(path)
  return path:match("([^/\\]+)$") or path
end

local function q(s)
  -- Quote a path for the shell.
  if IS_WIN then return '"' .. s .. '"' end
  return "'" .. s:gsub("'", "'\\''") .. "'"
end

local function sleep(sec)
  if bmd and bmd.wait then
    bmd.wait(sec)
  elseif IS_WIN then
    os.execute("ping -n " .. math.floor(sec + 1) .. " 127.0.0.1 > nul")
  else
    os.execute("sleep " .. sec)
  end
end

local function read_kv(path)
  local out = {}
  local f = io.open(path, "r")
  if not f then return out end
  for line in f:lines() do
    local k, v = line:match("^([%w_]+)=(.*)$")
    if k then out[k] = v:gsub("\r$", "") end
  end
  f:close()
  return out
end

local function safe_name(s)
  return (s:gsub("%.[^%.]+$", ""):gsub("[^%w%-_]+", "_"))
end

local function tc_to_frames(tc, fps)
  local h, m, s, f = tc:match("(%d+)[:;](%d+)[:;](%d+)[:;.](%d+)")
  if not h then return nil end
  local base = math.floor(fps + 0.5)
  return ((tonumber(h) * 60 + tonumber(m)) * 60 + tonumber(s)) * base + tonumber(f)
end

------------------------------------------------------------------------------
-- Find the clip under the playhead
------------------------------------------------------------------------------

local function get_resolve()
  if resolve then return resolve end
  if Resolve then return Resolve() end
  if bmd and bmd.scriptapp then return bmd.scriptapp("Resolve") end
  return nil
end

local function main()
  local res = get_resolve()
  if not res then
    log("Could not reach DaVinci Resolve. Run this from Workspace > Scripts inside Resolve.")
    return false
  end

  local project = res:GetProjectManager():GetCurrentProject()
  local timeline = project and project:GetCurrentTimeline()
  if not timeline then
    log("Open a timeline first.")
    return false
  end

  local item = timeline:GetCurrentVideoItem()
  if not item then
    log("Put the playhead over a video clip, then run FreeMask again.")
    return false
  end

  local mpi = item:GetMediaPoolItem()
  local path = mpi and mpi:GetClipProperty("File Path")
  if type(path) == "table" then path = path["File Path"] end
  if not path or path == "" then
    log("That clip has no media file (generators, titles and compound clips can't be masked).")
    return false
  end

  -- Image sequences show up as "name_[0001-0100].exr": use the folder.
  local source = path
  if path:match("%[%d+%-%d+%]") then source = dirname(path) end

  local left = math.floor(tonumber(item:GetLeftOffset() or 0) or 0)
  local duration = math.floor(tonumber(item:GetDuration() or 1) or 1)
  local first, last = left, left + duration - 1

  -- Which source frame is the playhead on?
  local frame = first
  local ok, fps = pcall(function()
    return tonumber(timeline:GetSetting("timelineFrameRate"))
  end)
  local tc = timeline.GetCurrentTimecode and timeline:GetCurrentTimecode()
  if ok and fps and tc then
    local abs = tc_to_frames(tc, fps)
    if abs then frame = math.max(first, math.min(last, left + abs - item:GetStart())) end
  end

  local name = safe_name(basename(path)) .. "_" .. first .. "-" .. last
  local out_dir = join(dirname(path), safe_name(basename(path)) .. "_freemask")
  local done_file = join(out_dir, "." .. name .. ".done")
  local log_file = join(out_dir, name .. "_freemask.log")
  local session = join(out_dir, name .. "_session.json")

  ------------------------------------------------------------------------------
  -- Launch FreeMask and wait for it to finish
  ------------------------------------------------------------------------------

  local python
  if IS_WIN then
    python = join(FREEMASK_HOME, ".venv", "Scripts", "pythonw.exe")
  else
    python = join(FREEMASK_HOME, ".venv", "bin", "python")
  end
  if not exists(python) then
    log("FreeMask isn't installed at " .. FREEMASK_HOME .. " — run its installer again.")
    return false
  end

  if IS_WIN then
    os.execute('mkdir ' .. q(out_dir) .. ' 2> nul')
  else
    os.execute('mkdir -p ' .. q(out_dir))
  end
  os.remove(done_file)
  os.remove(done_file .. ".alive")

  local args = {
    "-m", "freemask", q(source),
    "--start", tostring(first), "--end", tostring(last), "--frame", tostring(frame),
    "--out", q(out_dir), "--name", q(name),
    "--done-file", q(done_file), "--log", q(log_file),
  }
  if exists(session) then
    table.insert(args, "--session")
    table.insert(args, q(session))
  end

  local cmd
  if IS_WIN then
    cmd = 'start "FreeMask" /D ' .. q(FREEMASK_HOME) .. ' ' .. q(python) .. ' ' .. table.concat(args, " ")
  else
    cmd = 'cd ' .. q(FREEMASK_HOME) .. ' && nohup ' .. q(python) .. ' ' .. table.concat(args, " ")
      .. ' > /dev/null 2>&1 &'
  end
  log("Opening " .. basename(path) .. " (frames " .. first .. "-" .. last .. ") in FreeMask…")
  os.execute(cmd)

  -- Wait for FreeMask to finish. It writes a heartbeat every few seconds; if that
  -- stops (crash, force-quit, computer slept mid-launch…) we stop waiting.
  local alive_file = done_file .. ".alive"
  local launched = os.time()
  while not exists(done_file) do
    sleep(1)
    local f = io.open(alive_file, "r")
    local beat = f and tonumber(f:read("*l") or "")
    if f then f:close() end
    local now = os.time()
    if (beat and now - beat > HEARTBEAT_TIMEOUT) or (not beat and now - launched > STARTUP_TIMEOUT) then
      if not exists(done_file) then
        os.remove(alive_file)
        log("FreeMask stopped responding or closed unexpectedly. Details: " .. log_file)
        return false
      end
    end
  end
  local result = read_kv(done_file)
  os.remove(done_file)
  os.remove(alive_file)

  if result.error then
    log("FreeMask stopped with an error: " .. result.error)
    log("Details: " .. log_file)
    return false
  end
  if not (result.png or result.cutout or result.video) then
    log("Closed without exporting — nothing to import.")
    return true
  end

  ------------------------------------------------------------------------------
  -- Bring the mattes into Resolve
  ------------------------------------------------------------------------------

  local mp = project:GetMediaPool()
  local root = mp:GetRootFolder()
  local bin
  for _, f in pairs(root:GetSubFolderList() or {}) do
    if f:GetName() == "FreeMask" then bin = f end
  end
  bin = bin or mp:AddSubFolder(root, "FreeMask")
  if bin then mp:SetCurrentFolder(bin) end

  local count = tonumber(result.frame_count) or duration
  local first_out = tonumber(result.first_frame) or first
  local rname = result.name or name

  local function import_sequence(dir, suffix)
    local items = mp:ImportMedia({ {
      FilePath = join(dir, rname .. "_" .. suffix .. "_%06d.png"),
      StartIndex = first_out,
      EndIndex = first_out + count - 1,
    } })
    return items and items[1]
  end

  local imported = {}
  if result.png then imported.matte = import_sequence(result.png, "matte") end
  if result.cutout then imported.cutout = import_sequence(result.cutout, "cutout") end
  if result.video then
    local items = mp:ImportMedia({ result.video })
    imported.movie = items and items[1]
  end
  local n = 0
  for _ in pairs(imported) do n = n + 1 end
  log("Imported " .. n .. " item(s) into the FreeMask bin.")

  -- Lay the cutout (or matte) on a new track directly above the clip.
  local place = imported.cutout or imported.movie or imported.matte
  if place then
    local placed = pcall(function()
      timeline:AddTrack("video")
      local track = timeline:GetTrackCount("video")
      pcall(function() timeline:SetTrackName("video", track, "FreeMask") end)
      local r = mp:AppendToTimeline({ {
        mediaPoolItem = place,
        startFrame = 0,
        endFrame = count - 1,
        trackIndex = track,
        recordFrame = item:GetStart(),
        mediaType = 1,
      } })
      assert(r and #r > 0, "AppendToTimeline returned nothing")
    end)
    if placed then
      log("Placed on a new track above your clip.")
    else
      log("Mattes are in the FreeMask bin (couldn't place on the timeline automatically).")
    end
  end
  return true
end

local ok, err = pcall(main)
if not ok then log("Error: " .. tostring(err)) end
FREEMASK_RESULT = ok and err
