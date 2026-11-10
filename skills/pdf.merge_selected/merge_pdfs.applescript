-- merge_pdfs.applescript: merge the PDFs selected in Finder into one new PDF.
--
-- Does: keeps regular .pdf files, needs at least two, orders them by file name
--   (natural order), appends their pages with PDFKit, and writes
--   "Merged <yyyy-mm-dd>.pdf" in the folder of the first file ("... 2.pdf",
--   "... 3.pdf" if taken). Returns "merged N PDFs into <path>".
-- Never does: overwrite, move or delete anything, change the originals, or
--   run a shell command.
-- Argv contract:
--   selection              the mode the manifest uses. Reads Finder's selection.
--                          Every PDF must be inside ~/Downloads, ~/Documents or ~/Desktop.
--   files <abs path> ...   TEST-ONLY, never in the manifest. Same merge from
--                          explicit absolute paths. The folder check also accepts
--                          paths under $TMPDIR.
-- Needs: Automation permission for Finder (selection mode only).

use AppleScript version "2.4"
use framework "Foundation"
use framework "Quartz"

on run argv
	if (count of argv) is 0 then error "merge_pdfs: missing mode (selection or files)"
	set mode to item 1 of argv
	set strictFolders to true
	if mode is "selection" then
		if (count of argv) is not 1 then error "merge_pdfs: selection mode takes no paths"
		set candidates to my selectedPaths()
	else if mode is "files" then
		set strictFolders to false
		if (count of argv) is 1 then
			set candidates to {}
		else
			set candidates to items 2 thru -1 of argv
		end if
	else
		error "merge_pdfs: unknown mode " & mode
	end if

	set roots to my allowedRoots(strictFolders)
	set pdfPaths to my pdfsOnly(candidates, roots)
	if (count of pdfPaths) < 2 then error "select at least two PDFs in Finder"

	set sortedPaths to my sortedByName(pdfPaths)
	set merged to my mergeAll(sortedPaths)

	set outPath to my freshOutputPath(item 1 of sortedPaths)
	if ((merged's pageCount()) as integer) is 0 then error "merge_pdfs: the merged PDF has no pages"
	set outURL to current application's NSURL's fileURLWithPath:outPath
	if not (merged's writeToURL:outURL) then error "merge_pdfs: could not write " & outPath
	return "merged " & (count of sortedPaths) & " PDFs into " & outPath
end run

-- Paths of the Finder selection, as POSIX text.
on selectedPaths()
	tell application "Finder"
		set picked to selection as alias list
	end tell
	set out to {}
	repeat with a in picked
		set end of out to POSIX path of a
	end repeat
	return out
end selectedPaths

-- The folders a PDF may live in. Files mode also accepts $TMPDIR.
on allowedRoots(strictFolders)
	set homeDir to current application's NSHomeDirectory() as text
	set roots to {homeDir & "/Downloads", homeDir & "/Documents", homeDir & "/Desktop"}
	if not strictFolders then
		set tmpDir to current application's NSProcessInfo's processInfo()'s environment()'s objectForKey:"TMPDIR"
		if tmpDir is not missing value then set end of roots to (tmpDir as text)
	end if
	set out to {}
	repeat with r in roots
		set end of out to my canonical(r as text)
	end repeat
	return out
end allowedRoots

-- Keep the regular .pdf files; fail on a missing file or one outside the roots.
on pdfsOnly(candidates, roots)
	set kept to {}
	repeat with c in candidates
		set p to my cleanPath(c as text)
		if my extensionOf(p) is "pdf" then
			if not my pathExists(p) then error "merge_pdfs: file not found: " & p
			if not my isDirectory(p) then
				if not my isInside(my canonical(p), roots) then
					error "merge_pdfs: every PDF must be inside Downloads, Documents or Desktop: " & p
				end if
				set end of kept to p
			end if
		end if
	end repeat
	return kept
end pdfsOnly

-- Natural (Finder-style) order by file name.
on sortedByName(paths)
	set arr to current application's NSMutableArray's array()
	repeat with p in paths
		arr's addObject:(current application's NSURL's fileURLWithPath:p)
	end repeat
	set desc to current application's NSSortDescriptor's sortDescriptorWithKey:"lastPathComponent" ascending:true selector:(current application's NSSelectorFromString("localizedStandardCompare:"))
	set sortedArr to arr's sortedArrayUsingDescriptors:{desc}
	set out to {}
	repeat with s in ((sortedArr's valueForKey:"path") as list)
		set end of out to (s as text)
	end repeat
	return out
end sortedByName

-- Append every page of every PDF, in order, into one new document.
on mergeAll(paths)
	set merged to current application's PDFDocument's alloc()'s init()
	repeat with p in paths
		set doc to current application's PDFDocument's alloc()'s initWithURL:(current application's NSURL's fileURLWithPath:p)
		if doc is missing value then error "merge_pdfs: could not read " & p
		if (doc's isLocked()) then error "merge_pdfs: password-protected PDF: " & p
		set n to (doc's pageCount()) as integer
		repeat with i from 0 to n - 1
			set pg to (doc's pageAtIndex:i)
			merged's insertPage:(pg's |copy|()) atIndex:(merged's pageCount())
		end repeat
	end repeat
	return merged
end mergeAll

-- "<folder of first>/Merged <yyyy-mm-dd>.pdf", with " 2", " 3"... if taken.
on freshOutputPath(firstPath)
	set fm to current application's NSFileManager's defaultManager()
	set firstDir to ((current application's NSString's stringWithString:firstPath)'s stringByDeletingLastPathComponent()) as text
	set stamp to my todayStamp()
	set outPath to firstDir & "/Merged " & stamp & ".pdf"
	set k to 2
	repeat while (fm's fileExistsAtPath:outPath)
		set outPath to firstDir & "/Merged " & stamp & " " & k & ".pdf"
		set k to k + 1
	end repeat
	return outPath
end freshOutputPath

on todayStamp()
	set df to current application's NSDateFormatter's alloc()'s init()
	df's setLocale:(current application's NSLocale's localeWithLocaleIdentifier:"en_US_POSIX")
	df's setCalendar:(current application's NSCalendar's alloc()'s initWithCalendarIdentifier:(current application's NSCalendarIdentifierGregorian))
	df's setDateFormat:"yyyy-MM-dd"
	return (df's stringFromDate:(current application's NSDate's |date|())) as text
end todayStamp

-- A POSIX path, absolute and standardised (no trailing slash).
on cleanPath(raw)
	if raw does not start with "/" then error "merge_pdfs: paths must be absolute: " & raw
	return ((current application's NSString's stringWithString:raw)'s stringByStandardizingPath()) as text
end cleanPath

on canonical(p)
	set s to current application's NSString's stringWithString:p
	return ((s's stringByStandardizingPath()'s stringByResolvingSymlinksInPath()) as text)
end canonical

on extensionOf(p)
	return ((current application's NSString's stringWithString:p)'s pathExtension()'s lowercaseString()) as text
end extensionOf

on pathExists(p)
	set fm to current application's NSFileManager's defaultManager()
	return (fm's fileExistsAtPath:p) as boolean
end pathExists

on isDirectory(p)
	set fm to current application's NSFileManager's defaultManager()
	set flag to reference
	-- ASObjC returns {exists, isDirectory} for an out-parameter
	set res to (fm's fileExistsAtPath:p isDirectory:flag)
	if not (item 1 of res) then return false
	return ((item 2 of res) as boolean)
end isDirectory

-- True when p equals a root or sits under one (case-sensitive).
on isInside(p, roots)
	considering case
		repeat with r in roots
			set rt to r as text
			if p is rt or p starts with (rt & "/") then return true
		end repeat
	end considering
	return false
end isInside
