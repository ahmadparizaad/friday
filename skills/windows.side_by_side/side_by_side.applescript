-- side_by_side.applescript
-- Puts two apps' front windows side by side: the left app fills the left half
-- of the main display's usable area, the right app fills the right half.
--
-- argv: {leftAppName, rightAppName}  (exact installed app names from the core)
-- Does: activates both apps, reads the usable screen area (NSScreen.visibleFrame,
--       so the menu bar and Dock are excluded), then sets the position and size
--       of each app's front window through System Events.
-- Never: closes, quits, deletes, types, or runs a shell command. It only moves
--        and resizes windows.
-- Success output: "arranged <left> left and <right> right"
-- Errors: same app for both sides, unknown app, app with no open window.

use AppleScript version "2.4"
use scripting additions
use framework "AppKit"

on run argv
	if (count of argv) is not 2 then error "expected two app names: left and right"
	set leftName to my cleanName(item 1 of argv)
	set rightName to my cleanName(item 2 of argv)
	if leftName = rightName then error "left and right are the same app: " & leftName

	my activateApp(leftName)
	my activateApp(rightName)
	if not (my waitForWindow(leftName)) then error leftName & " has no open window"
	if not (my waitForWindow(rightName)) then error rightName & " has no open window"

	set area to my usableScreenArea()
	set areaX to item 1 of area
	set areaY to item 2 of area
	set areaW to item 3 of area
	set areaH to item 4 of area
	set halfW to areaW div 2

	my placeWindow(leftName, areaX, areaY, halfW, areaH)
	my placeWindow(rightName, areaX + halfW, areaY, areaW - halfW, areaH)

	return "arranged " & leftName & " left and " & rightName & " right"
end run

-- Validate one app name. Names come from the core's resolver, but the script
-- still refuses anything empty, oversized, or multi-line.
on cleanName(raw)
	set nm to raw as text
	if nm is "" or (count of nm) > 100 then error "invalid app name"
	if nm contains linefeed or nm contains return then error "invalid app name"
	return nm
end cleanName

on activateApp(appName)
	try
		tell application appName to activate
	on error
		error appName & " is not an installed app"
	end try
end activateApp

-- A freshly activated app can take a moment to open its window.
on waitForWindow(procName)
	repeat 12 times
		tell application "System Events"
			if exists process procName then
				if (count of windows of process procName) > 0 then return true
			end if
		end tell
		delay 0.25
	end repeat
	return false
end waitForWindow

on placeWindow(procName, x, y, w, h)
	tell application "System Events"
		tell process procName
			set position of window 1 to {x, y}
			set size of window 1 to {w, h}
		end tell
	end tell
end placeWindow

-- The main display's visible frame in System Events coordinates (origin at the
-- top-left of the primary screen). Returns {x, y, width, height}.
on usableScreenArea()
	set primaryScreen to (current application's NSScreen's screens())'s objectAtIndex:0
	set fullFrame to primaryScreen's frame()
	set visFrame to primaryScreen's visibleFrame()
	set fullHeight to height of |size| of fullFrame
	-- Cocoa's origin is bottom-left; flip the visible area's top edge.
	set topY to fullHeight - (y of |origin| of visFrame) - (height of |size| of visFrame)
	set areaX to round (x of |origin| of visFrame)
	set areaY to round topY
	set areaW to round (width of |size| of visFrame)
	set areaH to round (height of |size| of visFrame)
	return {areaX, areaY, areaW, areaH}
end usableScreenArea
