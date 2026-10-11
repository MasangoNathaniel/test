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

local LOG_PATH = nil  -- set once we know the output folder

local function log(msg)
  local line = "[FreeMask] " .. tostring(msg)
  print(line)
  if LOG_PATH then
    local f = io.open(LOG_PATH, "a")
    if f then f:write(os.date("%H:%M:%S "), line, "\n"); f:close() end
  end
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
  local resolve_log = join(out_dir, name .. "_resolve.log")
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
  os.remove(done_file .. ".result")
  LOG_PATH = resolve_log
  log("Resolve " .. tostring(res.GetVersionString and res:GetVersionString() or "?") .. ", clip " .. path)

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
  if not (result.png or result.cutout or result.video or result.cutout_video) then
    log("Closed without exporting — nothing to import.")
    return true
  end

  ------------------------------------------------------------------------------
  -- Bring the mattes into Resolve
  ------------------------------------------------------------------------------

  -- Tell the FreeMask window how it went (it waits for this and shows it).
  local function report(ok_flag, message)
    log(message)
    local f = io.open(done_file .. ".result", "w")
    if f then
      f:write("ok=", ok_flag and "1" or "0", "\n", "message=", message, "\n", "log=", LOG_PATH or "", "\n")
      f:close()
    end
    return ok_flag
  end

  local mp = project:GetMediaPool()
  local root = mp:GetRootFolder()
  local bin
  for _, f in pairs(root:GetSubFolderList() or {}) do
    if f:GetName() == "FreeMask" then bin = f end
  end
  bin = bin or mp:AddSubFolder(root, "FreeMask")
  if bin then mp:SetCurrentFolder(bin) end
  log("Media Pool bin: " .. tostring(bin and bin:GetName()))

  local count = tonumber(result.frame_count) or duration
  local first_out = tonumber(result.first_frame) or first
  local rname = result.name or name

  local function first_item(items)
    if type(items) ~= "table" then return nil end
    if items[1] then return items[1] end
    for _, v in pairs(items) do return v end
    return nil
  end

  local function import_file(path)
    local ok_i, items = pcall(function() return mp:ImportMedia({ path }) end)
    local it = ok_i and first_item(items)
    if not it then
      log("ImportMedia didn't return a clip for " .. path .. " (" .. tostring(items) .. "); trying Media Storage")
      local ok2, items2 = pcall(function() return res:GetMediaStorage():AddItemListToMediaPool({ path }) end)
      it = ok2 and first_item(items2)
    end
    log((it and "Imported " or "Could not import ") .. path)
    return it
  end

  local function import_sequence(dir, suffix)
    local ok_i, items = pcall(function()
      return mp:ImportMedia({ {
        FilePath = join(dir, rname .. "_" .. suffix .. "_%06d.png"),
        StartIndex = first_out,
        EndIndex = first_out + count - 1,
      } })
    end)
    local it = ok_i and first_item(items)
    if not it then
      local ok2, items2 = pcall(function() return res:GetMediaStorage():AddItemListToMediaPool({ dir }) end)
      it = ok2 and first_item(items2)
    end
    log((it and "Imported sequence " or "Could not import sequence ") .. dir)
    return it
  end

  local imported = {}
  -- Movies first: single files import far more reliably than image sequences.
  if result.cutout_video then imported.cutout_movie = import_file(result.cutout_video) end
  if result.video then imported.matte_movie = import_file(result.video) end
  if result.cutout then imported.cutout_seq = import_sequence(result.cutout, "cutout") end
  if result.png then imported.matte_seq = import_sequence(result.png, "matte") end
  if imported.cutout_movie then
    pcall(function() imported.cutout_movie:SetClipProperty("Alpha mode", "Straight") end)
  end

  local n = 0
  for _ in pairs(imported) do n = n + 1 end
  if n == 0 then
    return report(false, "Resolve wouldn't import the exported files. They're in " .. out_dir
      .. " — drag them into the Media Pool.")
  end

  ------------------------------------------------------------------------------
  -- Put the cutout (or matte) on a new track right above the clip
  ------------------------------------------------------------------------------

  local function track_count(t)
    local ok_c, items = pcall(function() return timeline:GetItemListInTrack("video", t) end)
    local c = 0
    if ok_c and type(items) == "table" then for _ in pairs(items) do c = c + 1 end end
    return c
  end

  local record = item:GetStart()
  pcall(function() timeline:AddTrack("video") end)
  local track = timeline:GetTrackCount("video")
  pcall(function() timeline:SetTrackName("video", track, "FreeMask") end)
  log("Placing on video track " .. tostring(track) .. " at timeline frame " .. tostring(record))

  local candidates = {
    { "cutout movie", imported.cutout_movie, 0 },
    { "cutout sequence", imported.cutout_seq, first_out },
    { "matte movie", imported.matte_movie, 0 },
    { "matte sequence", imported.matte_seq, first_out },
  }
  local placed_what
  for _, c in ipairs(candidates) do
    local label, mpi_c, seq_start = c[1], c[2], c[3]
    if mpi_c and not placed_what then
      local attempts = {
        { mediaPoolItem = mpi_c, startFrame = 0, endFrame = count - 1, trackIndex = track, recordFrame = record, mediaType = 1 },
        { mediaPoolItem = mpi_c, startFrame = 0, endFrame = count - 1, trackIndex = track, recordFrame = record },
        { mediaPoolItem = mpi_c, startFrame = seq_start, endFrame = seq_start + count - 1, trackIndex = track, recordFrame = record },
        { mediaPoolItem = mpi_c, trackIndex = track, recordFrame = record },
      }
      for i, info in ipairs(attempts) do
        local before = track_count(track)
        local ok_a, r = pcall(function() return mp:AppendToTimeline({ info }) end)
        local after = track_count(track)
        log(string.format("  %s, attempt %d: call %s, returned %s, items on track %d -> %d",
          label, i, ok_a and "ok" or ("failed: " .. tostring(r)), tostring(first_item(r)), before, after))
        if after > before then
          placed_what = label
          break
        end
      end
    end
  end

  if placed_what then
    return report(true, "Added the " .. placed_what .. " to your timeline on track V" .. track
      .. ", right above your clip. Everything is also in the FreeMask bin.")
  end
  pcall(function() timeline:DeleteTrack("video", track) end)
  return report(false, "Imported " .. n .. " item(s) into the FreeMask bin, but Resolve wouldn't place them "
    .. "on the timeline automatically. Drag the cutout from the FreeMask bin onto the track above your clip.")
end

local ok, err = pcall(main)
if not ok then log("Error: " .. tostring(err)) end
FREEMASK_RESULT = ok and err
