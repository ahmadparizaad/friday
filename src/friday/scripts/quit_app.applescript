-- Quit an allow-listed, installed application.
--
-- The app name arrives as an argv parameter, never interpolated into
-- script text, and is validated against the installed-apps scan before
-- we run (registry.py known_apps / executor.py _validate). Quitting can
-- discard unsaved in-app state, unlike opening or focusing one, which is
-- why this tool is MUTATE_LOCAL and always confirms (§5.4).
on run argv
	set appName to item 1 of argv
	tell application "System Events"
		set wasRunning to (exists process appName)
	end tell
	if wasRunning then
		tell application appName to quit
		return "quit " & appName
	else
		return appName & " was not running"
	end if
end run
