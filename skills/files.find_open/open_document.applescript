-- open_document.applescript (skill files.find_open)
--
-- Opens ONE existing document with Finder, so its default app shows it.
--
-- argv: exactly one absolute POSIX path (the core's resolved `file` slot).
-- Success line: "opened <file name>".
--
-- Never: deletes, moves, writes, runs a script, mounts a disk image, opens a
-- folder, or opens an app or any launch-capable file. The path is canonicalised
-- (".." removed, symlinks resolved) and must sit inside ~/Downloads,
-- ~/Documents or ~/Desktop. Every refusal is an `error`, so the run exits non-zero.

use AppleScript version "2.4"
use framework "Foundation"

property blockedExtensions : {"app", "command", "sh", "tool", "terminal", "workflow", "pkg", "scpt", "scptd", "applescript", "jar", "action", "prefpane", "dmg", "webloc", "fileloc", "inetloc", "url", "bash", "zsh", "py"}

on run argv
	if (count of argv) is not 1 then error "open_document expects exactly one file path"
	set rawPath to item 1 of argv as text
	if rawPath is "" then error "refusing to open: empty path"
	if character 1 of rawPath is not "/" then error "refusing to open: path must be absolute"

	set shownName to my lastComponent(rawPath)
	set resolvedPath to my canonical(rawPath)

	set home to (current application's NSHomeDirectory()) as text
	set roots to {my canonical(home & "/Downloads"), my canonical(home & "/Documents"), my canonical(home & "/Desktop")}
	if not my insideRoots(resolvedPath, roots) then error "refusing to open " & shownName & ": not in Downloads, Documents or Desktop"

	set fm to current application's NSFileManager's defaultManager()
	set isDirRef to a reference to false
	set itExists to fm's fileExistsAtPath:resolvedPath isDirectory:isDirRef
	if not itExists then error "no such file: " & shownName
	set isDir to (contents of isDirRef) as boolean
	if isDir then error "refusing to open " & shownName & ": not a document"

	-- check the name as given AND the real target, so a link named "x.app"
	-- or a link to an app or script is refused too
	if blockedExtensions contains my extensionOf(rawPath) then error "refusing to open " & shownName & ": not a document"
	if blockedExtensions contains my extensionOf(resolvedPath) then error "refusing to open " & shownName & ": not a document"

	tell application "Finder" to open (POSIX file resolvedPath as alias)
	return "opened " & shownName
end run

on canonical(p)
	set s to current application's NSString's stringWithString:p
	return (s's stringByStandardizingPath()'s stringByResolvingSymlinksInPath()) as text
end canonical

on lastComponent(p)
	return ((current application's NSString's stringWithString:p)'s lastPathComponent()) as text
end lastComponent

on extensionOf(p)
	return ((current application's NSString's stringWithString:p)'s pathExtension()'s lowercaseString()) as text
end extensionOf

on insideRoots(p, roots)
	repeat with r in roots
		if p starts with ((r as text) & "/") then return true
	end repeat
	return false
end insideRoots
