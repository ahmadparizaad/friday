-- Lock the screen. No arguments. Reversible by unlocking, same tier as
-- opening an app (NAVIGATE, §5.4).
on run argv
	tell application "System Events" to keystroke "q" using {control down, command down}
	return "locked screen"
end run
