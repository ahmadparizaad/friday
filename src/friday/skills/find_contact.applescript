-- Look up contacts whose name contains the query (argv item 1).
-- Prints one "name<US>email" line per address (US = ASCII 31, so a name
-- containing a tab cannot shift the email column). Read-only.
on run argv
    set q to item 1 of argv
    set out to ""
    tell application "Contacts"
        repeat with p in (people whose name contains q)
            repeat with e in (emails of p)
                set out to out & (name of p) & (ASCII character 31) & (value of e) & linefeed
            end repeat
        end repeat
    end tell
    return out
end run
