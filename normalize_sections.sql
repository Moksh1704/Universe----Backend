-- =============================================================================
-- UniVerse Attendance Fix — DB Section Normalization Migration
-- =============================================================================
-- Run this ONCE on your PostgreSQL databases to normalize section formats.
-- Converts 'CSE 06' → 'CSE06' (removes spaces, uppercases) in all relevant
-- tables so the frontend/backend queries match correctly.
--
-- Run on: universe_db (student app DB)
-- =============================================================================

-- Preview first — see what will change
SELECT DISTINCT section, COUNT(*) as student_count
FROM users
WHERE section IS NOT NULL
GROUP BY section
ORDER BY section;

-- Normalize sections in users table (student_db / universe_db)
UPDATE users
SET section = REPLACE(UPPER(section), ' ', '')
WHERE section IS NOT NULL
  AND section != REPLACE(UPPER(section), ' ', '');

-- Verify the result
SELECT DISTINCT section, COUNT(*) as student_count
FROM users
WHERE section IS NOT NULL
GROUP BY section
ORDER BY section;

-- Also normalize section in day_attendance if you have historical data
UPDATE day_attendance
SET section = REPLACE(UPPER(section), ' ', '')
WHERE section IS NOT NULL
  AND section != REPLACE(UPPER(section), ' ', '');

-- =============================================================================
-- OPTIONAL: If you have a separate timetable table in student_db,
-- normalize section there too.
-- =============================================================================
-- UPDATE timetables
-- SET section = REPLACE(UPPER(section), ' ', '')
-- WHERE section IS NOT NULL
--   AND section != REPLACE(UPPER(section), ' ', '');

-- =============================================================================
-- Confirmation query
-- =============================================================================
SELECT
    'users' as table_name,
    section,
    COUNT(*) as count
FROM users
WHERE section IS NOT NULL
GROUP BY section

UNION ALL

SELECT
    'day_attendance' as table_name,
    section,
    COUNT(*) as count
FROM day_attendance
WHERE section IS NOT NULL
GROUP BY section

ORDER BY table_name, section;