-- export_apple_notes.applescript
--
-- Dumps every Apple Note IN THE FOLDER named by `targetFolder` below (default
-- "Notes") to ~/AppleNotesExport/<Folder>/<Title>.html, one file
-- per note, each with a metadata header comment the Workshop importer reads
-- (stable note id, title, folder, dates). Run it on your Mac:
--
--     osascript scripts/export_apple_notes.applescript
--
-- or open it in Script Editor and hit Run. The first run pops a one-time
-- "Terminal/Script Editor wants to control Notes" permission prompt -- allow it.
--
-- It is safe to re-run: it overwrites the export folder from scratch each time,
-- and the importer on the Workshop side is idempotent, so re-exporting +
-- re-importing only adds new/changed notes.

on pad(n, w)
	set s to (n as integer) as string
	repeat while (length of s) < w
		set s to "0" & s
	end repeat
	return s
end pad

on isoDate(d)
	if d is missing value then return ""
	return (my pad(year of d, 4)) & "-" & (my pad((month of d) as integer, 2)) & "-" & (my pad(day of d, 2)) & "T" & (my pad(hours of d, 2)) & ":" & (my pad(minutes of d, 2)) & ":" & (my pad(seconds of d, 2))
end isoDate

-- Replace path-hostile characters so the filename is safe on any OS.
on sanitize(t)
	set t to t as string
	set out to ""
	repeat with i from 1 to length of t
		set c to character i of t
		if c is in {"/", ":", "\\", "*", "?", "\"", "<", ">", "|", "	"} then
			set out to out & " "
		else if (id of c) < 32 then
			set out to out & " "
		else
			set out to out & c
		end if
	end repeat
	-- collapse runs of spaces and trim
	repeat while out contains "  "
		set AppleScript's text item delimiters to "  "
		set parts to text items of out
		set AppleScript's text item delimiters to " "
		set out to parts as string
	end repeat
	set AppleScript's text item delimiters to ""
	set out to my trim(out)
	if out is "" then set out to "Untitled"
	if (length of out) > 120 then set out to text 1 thru 120 of out
	return out
end sanitize

on trim(t)
	set t to t as string
	repeat while (length of t) > 0 and (t starts with " " or t starts with ".")
		if (length of t) is 1 then
			set t to ""
		else
			set t to text 2 thru -1 of t
		end if
	end repeat
	repeat while (length of t) > 0 and (t ends with " " or t ends with ".")
		if (length of t) is 1 then
			set t to ""
		else
			set t to text 1 thru -2 of t
		end if
	end repeat
	return t
end trim

on writeFile(posixPath, textContent)
	set fileRef to open for access (POSIX file posixPath) with write permission
	try
		set eof of fileRef to 0
		write textContent to fileRef as «class utf8»
		close access fileRef
	on error errMsg
		try
			close access fileRef
		end try
		error errMsg
	end try
end writeFile

set homePath to POSIX path of (path to home folder)
set exportRoot to homePath & "AppleNotesExport/"
do shell script "mkdir -p " & quoted form of exportRoot

set exportedCount to 0
set usedNames to {} -- track "<folder>/<name>" to de-collide same-titled notes

-- Scope the export to a single Apple Notes folder. Set to "" to export every
-- note in every folder (the old behavior).
set targetFolder to "Notes"

tell application "Notes"
	if targetFolder is "" then
		set allNotes to every note
	else
		set allNotes to notes of folder targetFolder
	end if
end tell

repeat with theNote in allNotes
	tell application "Notes"
		set noteTitle to name of theNote
		set noteBody to body of theNote
		set noteId to id of theNote
		try
			set folderName to name of container of theNote
		on error
			set folderName to "Notes"
		end try
		try
			set createdISO to my isoDate(creation date of theNote)
		on error
			set createdISO to ""
		end try
		try
			set modifiedISO to my isoDate(modification date of theNote)
		on error
			set modifiedISO to ""
		end try
	end tell

	set safeFolder to my sanitize(folderName)
	set safeTitle to my sanitize(noteTitle)

	-- de-collide identical <folder>/<title> pairs within this export
	set baseKey to safeFolder & "/" & safeTitle
	set finalTitle to safeTitle
	set n to 2
	repeat while usedNames contains (safeFolder & "/" & finalTitle)
		set finalTitle to safeTitle & " " & n
		set n to n + 1
	end repeat
	set end of usedNames to (safeFolder & "/" & finalTitle)

	set folderPath to exportRoot & safeFolder & "/"
	do shell script "mkdir -p " & quoted form of folderPath
	set filePath to folderPath & finalTitle & ".html"

	set headerText to "<!-- workshop-apple-note" & linefeed & ¬
		"id: " & noteId & linefeed & ¬
		"title: " & noteTitle & linefeed & ¬
		"folder: " & folderName & linefeed & ¬
		"created: " & createdISO & linefeed & ¬
		"modified: " & modifiedISO & linefeed & ¬
		"-->" & linefeed

	my writeFile(filePath, headerText & noteBody)
	set exportedCount to exportedCount + 1
end repeat

set summary to "Exported " & exportedCount & " notes to " & exportRoot
display dialog summary buttons {"OK"} default button "OK"
return summary
