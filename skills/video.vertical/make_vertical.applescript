-- make_vertical.applescript
-- Makes the selected video vertical (1080x1920, 9:16) as a NEW file next to it.
--
-- What it does: takes the one video file selected in Finder, checks it is a
-- video inside ~/Downloads, ~/Documents, ~/Desktop or ~/Movies, picks an unused
-- name "<name>-vertical.mp4", and starts ffmpeg in the background to write it.
-- It returns at once; the encode keeps running after this script has finished.
-- What it never does: overwrite or delete anything, change the original file,
-- or send data anywhere.
--
-- ALLOWED EXCEPTION TO THE NO-SHELL RULE: this script uses `do shell script`.
-- Encoding takes longer than the 15 s step timeout, so the command must return
-- at once and run in the background. What keeps it safe:
--   * the ffmpeg path comes from a fixed candidate list and must exist;
--   * every path in the command goes through `quoted form of`;
--   * no other variable text goes into the command: the filter and codec
--     options are constants;
--   * ffmpeg runs with -n (never overwrite) and -nostdin, output to /dev/null.
--
-- argv contract:
--   selection          what the manifest passes: the Finder selection.
--                      Folder allow-list applies.
--   files <path>       TEST ONLY, never used by the manifest. Encodes the given
--                      POSIX path. The folder allow-list is skipped so a
--                      throwaway clip under $TMPDIR can be tested; the
--                      extension and file checks still apply.
-- Success line: "started making <out name> vertical; it will appear next to the original"

property ffmpegCandidates : {"/opt/homebrew/bin/ffmpeg", "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg", "/usr/local/bin/ffmpeg"}
property videoExtensions : {"mov", "mp4", "m4v", "mkv", "webm"}

on run argv
  if (count of argv) is 0 then error "usage: make_vertical.applescript selection"
  set modeName to item 1 of argv
  set homeP to POSIX path of (path to home folder)
  if modeName is "selection" then
    if (count of argv) is not 1 then error "selection mode takes no other arguments."
    set inPath to selectedVideoPath()
    set checkFolder to true
  else if modeName is "files" then
    if (count of argv) is not 2 then error "files mode takes exactly one path (test only)."
    set inPath to item 2 of argv
    set checkFolder to false
  else
    error "unknown mode " & modeName
  end if

  set nameParts to splitText(inPath, "/")
  if (count of nameParts) is less than 2 or (item 1 of nameParts) is not "" then error "The selected item is not a local file."
  if nameParts contains ".." then error "The selected path is not allowed."
  if checkFolder then
    set allowedPrefixes to {homeP & "Downloads/", homeP & "Documents/", homeP & "Desktop/", homeP & "Movies/"}
    if not anyPrefix(inPath, allowedPrefixes) then error "Select a video inside ~/Downloads, ~/Documents, ~/Desktop or ~/Movies."
  end if

  set dirPath to joinText(items 1 thru -2 of nameParts, "/") & "/"
  set fileName to item -1 of nameParts
  set extParts to splitText(fileName, ".")
  if (count of extParts) is less than 2 then error "The selected file has no video extension."
  set ext to item -1 of extParts
  set stem to joinText(items 1 thru -2 of extParts, ".")
  if stem is "" then error "The selected file has no usable name."
  if ext is not in videoExtensions then error "The selected file is not a video (mov, mp4, m4v, mkv or webm)."

  set isFolder to true
  try
    set isFolder to folder of (info for (POSIX file inPath))
  on error
    error "The selected file cannot be read."
  end try
  if isFolder then error "The selected item is a folder, not a video file."

  set outPath to uniqueOutput(dirPath, stem)
  set outName to item -1 of splitText(outPath, "/")

  set ffmpegPath to findFfmpeg()
  set shellCommand to quoted form of ffmpegPath & " -nostdin -n -i " & quoted form of inPath & " -vf \"crop=ih*9/16:ih,scale=1080:1920\" -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 128k -af loudnorm " & quoted form of outPath & " > /dev/null 2>&1 &"
  do shell script shellCommand

  return "started making " & outName & " vertical; it will appear next to the original"
end run

on selectedVideoPath()
  tell application "Finder"
    set chosen to selection as alias list
  end tell
  if (count of chosen) is 0 then error "Select one video file in Finder first."
  if (count of chosen) is greater than 1 then error "Select exactly one video file, not " & (count of chosen) & " items."
  return POSIX path of (item 1 of chosen)
end selectedVideoPath

on findFfmpeg()
  repeat with candidate in ffmpegCandidates
    if pathExists(contents of candidate) then return contents of candidate
  end repeat
  error "ffmpeg is not installed"
end findFfmpeg

on uniqueOutput(dirPath, stem)
  set candidate to stem & "-vertical.mp4"
  set n to 2
  repeat while pathExists(dirPath & candidate)
    set candidate to stem & "-vertical " & n & ".mp4"
    set n to n + 1
  end repeat
  return dirPath & candidate
end uniqueOutput

on pathExists(p)
  try
    info for (POSIX file p)
    return true
  on error
    return false
  end try
end pathExists

on anyPrefix(s, prefixes)
  repeat with pre in prefixes
    if (text 1 thru (length of (pre as text)) of s) is (pre as text) then return true
  end repeat
  return false
end anyPrefix

on splitText(t, delim)
  set oldDelims to AppleScript's text item delimiters
  set AppleScript's text item delimiters to delim
  set parts to text items of t
  set AppleScript's text item delimiters to oldDelims
  return parts
end splitText

on joinText(parts, delim)
  set oldDelims to AppleScript's text item delimiters
  set AppleScript's text item delimiters to delim
  set joined to parts as text
  set AppleScript's text item delimiters to oldDelims
  return joined
end joinText
