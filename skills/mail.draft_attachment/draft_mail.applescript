-- draft_mail.applescript (skill: mail.draft_attachment)
--
-- What it does:
--   Opens a NEW, VISIBLE outgoing message in Mail, adds one recipient and one
--   attachment, leaves the body empty, sets the subject to the file's name
--   without its extension, and brings Mail to the front. The user reads the
--   draft and presses Send themselves.
--
-- What it never does:
--   It NEVER sends mail. There is no `send` command anywhere in this script;
--   the message stays a draft. It never deletes or overwrites files and never
--   runs a shell. Undo = close the draft window without saving.
--
-- argv contract:
--   1 = recipient email address (the core resolved a contact name to it)
--   2 = absolute POSIX path of an EXISTING file inside ~/Downloads,
--       ~/Documents or ~/Desktop (the core resolved it with Spotlight)
--
-- Success output: "draft ready for <address> with <file name>"
-- Every argv value is untrusted data: it is validated here and only ever
-- used as a value, never built into code.

on run argv
    if (count of argv) is not 2 then error "draft_mail needs exactly two arguments: an address and a file"

    set recipientAddress to item 1 of argv
    set filePath to item 2 of argv

    if my isPlausibleEmail(recipientAddress) is false then error "not a valid email address: " & recipientAddress

    if filePath contains ".." then error "refusing a path containing '..': " & filePath
    set homePath to POSIX path of (path to home folder)
    if my isInsideAllowedFolder(filePath, homePath) is false then error "the file must be inside Downloads, Documents or Desktop: " & filePath

    try
        set fileRef to (POSIX file filePath) as alias
    on error
        error "the file does not exist: " & filePath
    end try

    set fileName to my lastPathItem(filePath)
    if my isBlockedExtension(my fileExtension(fileName)) then error "refusing to attach an executable or package: " & fileName

    set subjectText to my fileStem(fileName)

    tell application "Mail"
        set newMessage to make new outgoing message with properties {visible:true, subject:subjectText, content:""}
        tell newMessage
            make new to recipient at end of to recipients with properties {address:recipientAddress}
            tell content
                make new attachment with properties {file name:fileRef} at after the last paragraph
            end tell
        end tell
        activate
    end tell

    return "draft ready for " & recipientAddress & " with " & fileName
end run

-- A simple structural check: one "@", a non-empty local part, a dotted domain
-- with no empty labels, and none of the characters that could break a header.
on isPlausibleEmail(addr)
    if (length of addr) < 3 or (length of addr) > 254 then return false
    repeat with ch in characters of addr
        if (" ,;:<>()[]\"\\" contains (ch as text)) then return false
    end repeat

    set oldDelims to AppleScript's text item delimiters
    set AppleScript's text item delimiters to "@"
    set parts to text items of addr
    set AppleScript's text item delimiters to oldDelims
    if (count of parts) is not 2 then return false

    set localPart to item 1 of parts
    set domainPart to item 2 of parts
    if localPart is "" then return false
    if domainPart does not contain "." then return false
    if domainPart starts with "." or domainPart ends with "." then return false
    if domainPart contains ".." then return false
    return true
end isPlausibleEmail

-- True only when the path is strictly below one of the three allowed folders.
on isInsideAllowedFolder(p, homePath)
    repeat with folderName in {"Downloads", "Documents", "Desktop"}
        set folderPrefix to homePath & folderName & "/"
        if (length of p) > (length of folderPrefix) then
            considering case
                set leadingText to text 1 thru (length of folderPrefix) of p
            end considering
            if leadingText is folderPrefix then return true
        end if
    end repeat
    return false
end isInsideAllowedFolder

on lastPathItem(p)
    set oldDelims to AppleScript's text item delimiters
    set AppleScript's text item delimiters to "/"
    set parts to text items of p
    set AppleScript's text item delimiters to oldDelims
    return item -1 of parts
end lastPathItem

-- The text after the last dot, lower-cased; "" when there is no extension.
on fileExtension(fname)
    set oldDelims to AppleScript's text item delimiters
    set AppleScript's text item delimiters to "."
    set parts to text items of fname
    set AppleScript's text item delimiters to oldDelims
    if (count of parts) < 2 then return ""
    return do_lower(item -1 of parts)
end fileExtension

-- The file name without its extension (the whole name if that would be empty).
on fileStem(fname)
    set ext to my fileExtension(fname)
    if ext is "" then return fname
    set stemLength to (length of fname) - (length of ext) - 1
    if stemLength < 1 then return fname
    return text 1 thru stemLength of fname
end fileStem

on isBlockedExtension(ext)
    return ({"app", "command", "pkg", "scpt", "scptd", "workflow", "sh", "terminal", "tool"} contains ext)
end isBlockedExtension

on do_lower(t)
    set lowerChars to "abcdefghijklmnopqrstuvwxyz"
    set upperChars to "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    set out to ""
    repeat with ch in characters of t
        set pos to offset of (ch as text) in upperChars
        if pos > 0 then
            set out to out & (character pos of lowerChars)
        else
            set out to out & (ch as text)
        end if
    end repeat
    return out
end do_lower
