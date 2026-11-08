-- Bring an allow-listed application to the front, launching it only if
-- it is not already running.
--
-- This merges what were two tools. Whether an app is running is machine
-- state, so the executor looks it up rather than asking the model to
-- guess it from the phrasing -- "open safari" and "switch to safari"
-- mean the same thing to a user whose Safari is already open.
--
-- The app name arrives as an argv parameter, never interpolated into
-- script text, and is validated against the registry enum before we run.
on run argv
	set appName to item 1 of argv
	tell application "System Events"
		set wasRunning to (exists process appName)
	end tell
	tell application appName to activate
	if wasRunning then
		return "focused " & appName
	else
		return "opened " & appName
	end if
end run
