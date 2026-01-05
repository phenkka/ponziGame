WITH ranked AS (
    SELECT id_opening,
           id_purchase,
           ROW_NUMBER() OVER (PARTITION BY id_purchase ORDER BY id_opening) AS rn
    FROM Chest_openings
    WHERE id_purchase IS NOT NULL
)
UPDATE Chest_openings co
SET id_purchase = NULL
FROM ranked r
WHERE co.id_opening = r.id_opening
  AND r.rn > 1;

CREATE UNIQUE INDEX IF NOT EXISTS ux_chest_openings_id_purchase
ON Chest_openings(id_purchase)
WHERE id_purchase IS NOT NULL;
