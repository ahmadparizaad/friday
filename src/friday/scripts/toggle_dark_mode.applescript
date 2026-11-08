-- Set light or dark mode, or flip it. One argument: dark | light | toggle.
--
-- The mode arrives as an argv parameter already resolved to one of a
-- closed enum by slots.py and re-validated by executor.py -- never
-- interpolated into script text.
on run argv
	set requested to item 1 of argv
	tell application "System Events"
		tell appearance preferences
			if requested is "dark" then
				set dark mode to true
			else if requested is "light" then
				set dark mode to false
			else if requested is "toggle" then
				set dark mode to not dark mode
			else
				error "unknown appearance mode: " & requested
			end if
			set isDark to dark mode
		end tell
	end tell
	if isDark then
		return "dark mode is on"
	else
		return "light mode is on"
	end if
end run
