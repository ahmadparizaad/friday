-- tidy_downloads.applescript
--
-- Moves loose files directly inside ~/Downloads into subfolders by extension:
--   Images, Documents, Videos, Audio, Archives, Installers.
--
-- Never: deletes or trashes anything, overwrites anything, moves folders,
-- aliases or hidden files, or touches in-progress downloads (.download,
-- .crdownload, .part). A file whose extension is not in the table stays put.
-- Subfolders are created only when a file needs them. On a name clash in the
-- target folder the file is renamed "name 2.ext", "name 3.ext", ... first.
-- If one move fails the rest still run; a file that could not move stays put.
--
-- Argv contract: run   (no slot values; any extra argv is ignored)
-- Output: "moved N files into folders" (N may be 0), plus
-- ", skipped M that could not be moved" when some files stayed put.
-- Uses Finder only. No "do shell script".

property categoryNames : {"Images", "Documents", "Videos", "Audio", "Archives", "Installers"}

on run argv
  set movedCount to 0
  set plannedCount to 0
  tell application "Finder"
    set dl to folder "Downloads" of home
    set rootNames to name of every file of dl
    set rootVisible to visible of every file of dl
  end tell
  set plan to my buildPlan(rootNames, rootVisible)
  repeat with wanted in categoryNames
    set namesHere to my namesFor(plan, wanted)
    if (count of namesHere) > 0 then
      set plannedCount to plannedCount + (count of namesHere)
      set movedCount to movedCount + (my tidyCategory(dl, wanted, namesHere, rootNames))
    end if
  end repeat
  set outText to "moved " & movedCount & " files into folders"
  -- a locked or busy file stays in Downloads; say so rather than hide it
  set skippedCount to plannedCount - movedCount
  if skippedCount > 0 then set outText to outText & ", skipped " & skippedCount & " that could not be moved"
  return outText
end run

-- Pick the loose, visible, non-hidden files that belong to a category.
on buildPlan(rootNames, rootVisible)
  set plan to {}
  repeat with i from 1 to count of rootNames
    set nm to item i of rootNames
    if (item i of rootVisible) and ((text 1 of nm) is not ".") then
      set wantCat to my categoryFor(my extensionOf(nm))
      if wantCat is not "" then set end of plan to {fileName:nm, catName:wantCat}
    end if
  end repeat
  return plan
end buildPlan

on namesFor(plan, wanted)
  set out to {}
  repeat with r in plan
    if (catName of r) is wanted then set end of out to fileName of r
  end repeat
  return out
end namesFor

-- Move one category's files. Returns how many left Downloads.
on tidyCategory(dl, wanted, namesHere, rootNames)
  tell application "Finder"
    if not (exists folder wanted of dl) then
      make new folder at dl with properties {name:wanted}
    end if
    set destFolder to folder wanted of dl
    set existing to name of every item of destFolder
  end tell
  set reserved to existing & rootNames
  set movers to {}
  repeat with nm in namesHere
    set finalName to nm as text
    if my containsText(existing, nm) then
      set finalName to ""
      set rawExt to my extensionOf(nm)
      set stemText to text 1 thru ((count of nm) - (count of rawExt) - 1) of nm
      set newName to my uniqueName(stemText, rawExt, reserved)
      try
        tell application "Finder" to set name of file nm of dl to newName
        set finalName to newName
      end try
    end if
    if finalName is not "" then
      set end of movers to finalName
      set reserved to reserved & {finalName}
    end if
  end repeat
  if (count of movers) is 0 then return 0
  set refs to {}
  tell application "Finder"
    repeat with nm in movers
      set end of refs to file nm of dl
    end repeat
  end tell
  try
    tell application "Finder" to move refs to destFolder
  on error
    -- Bulk move failed (for example one file is locked): try each one alone.
    repeat with nm in movers
      try
        tell application "Finder" to move file nm of dl to destFolder
      end try
    end repeat
  end try
  tell application "Finder"
    set remaining to name of every file of dl
  end tell
  set movedHere to 0
  repeat with nm in movers
    if not (my containsText(remaining, nm)) then set movedHere to movedHere + 1
  end repeat
  return movedHere
end tidyCategory

-- "report.pdf" with "report.pdf" taken -> "report 2.pdf", then "report 3.pdf"...
on uniqueName(stemText, extText, reserved)
  set n to 2
  repeat
    set candidate to stemText & " " & n & "." & extText
    if not (my containsText(reserved, candidate)) then return candidate
    set n to n + 1
  end repeat
end uniqueName

-- Text after the last dot, or "" when there is no extension.
on extensionOf(fileName)
  set dotPos to 0
  repeat with i from (count of fileName) to 1 by -1
    if character i of fileName is "." then
      set dotPos to i
      exit repeat
    end if
  end repeat
  if dotPos is 0 or dotPos is (count of fileName) then return ""
  return text (dotPos + 1) thru -1 of fileName
end extensionOf

-- Case-insensitive extension table. "" means the file stays where it is.
on categoryFor(ext)
  ignoring case
    if ext is in {"download", "crdownload", "part"} then return ""
    if ext is in {"jpg", "jpeg", "png", "gif", "heic", "webp", "svg", "tiff", "bmp"} then return "Images"
    if ext is in {"pdf", "doc", "docx", "txt", "rtf", "pages", "md", "csv", "xls", "xlsx", "numbers", "ppt", "pptx", "key"} then return "Documents"
    if ext is in {"mov", "mp4", "m4v", "avi", "mkv", "webm"} then return "Videos"
    if ext is in {"mp3", "m4a", "wav", "aac", "flac"} then return "Audio"
    if ext is in {"zip", "rar", "7z", "tar", "gz", "tgz"} then return "Archives"
    if ext is in {"dmg", "pkg"} then return "Installers"
  end ignoring
  return ""
end categoryFor

on containsText(theList, theText)
  ignoring case
    return (theText is in theList)
  end ignoring
end containsText
