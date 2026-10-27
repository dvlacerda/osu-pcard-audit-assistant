-- =====================================================================
-- P-Card audit - Oklahoma State University (AgencyNumber 1000), calendar 2014
-- SQLite queries for Part II (Question1-Question14) and Part III (qry_T3_...)
--
-- Notes on the data
--   * Year and Month equal the year and month of TransactionDate for every row
--     (verified on all 489,178 rows), so Year = 2014 selects calendar-year 2014.
--   * TransactionDate is stored as text 'M/D/YYYY 0:00:00'. Text sorting would
--     put '10/1/2014' before '2/1/2014', so queries that sort by date build a
--     proper ISO date (TxDate) from Year, Month and the day part of the string.
--   * Amounts are net of returns (credits are negative) unless stated otherwise.
-- =====================================================================


-- =====================================================================
-- PART II - INTERNAL-CONTROL TESTS
-- =====================================================================

-- name: Question1
-- Control 1: a user shall not spend more than $50,000 per year.
SELECT FullName,
       ROUND(SUM(Amount), 2) AS TotalSpent2014,
       COUNT(*)              AS Transactions
FROM pcards
WHERE AgencyNumber = 1000
  AND Year = 2014
GROUP BY FullName
HAVING SUM(Amount) > 50000
ORDER BY TotalSpent2014 DESC;


-- name: Question2
-- Control 2: a user shall not spend more than $10,000 per month without approval.
SELECT FullName,
       ROUND(SUM(Amount), 2) AS TotalSpentInMonth,
       Month
FROM pcards
WHERE AgencyNumber = 1000
  AND Year = 2014
GROUP BY FullName, Month
HAVING SUM(Amount) > 10000
ORDER BY Month ASC, TotalSpentInMonth DESC;


-- name: Question3
-- Control 3: a user shall not spend more than $5,000 per transaction.
SELECT Amount, FullName, Description, Vendor, TransactionDate, PostedDate, MCC
FROM pcards
WHERE AgencyNumber = 1000
  AND Year = 2014
  AND Amount > 5000
ORDER BY Amount DESC;


-- name: Question4
-- Control 4: an amount > $5,000 should not be split between two or more swipes
-- by the same person (same cardholder + same vendor + same day).
WITH osu2014 AS (
    SELECT *,
           date(printf('%d-%02d-%02d', Year, Month,
                CAST(substr(TransactionDate, instr(TransactionDate, '/') + 1, 2) AS INTEGER))) AS TxDate
    FROM pcards
    WHERE AgencyNumber = 1000 AND Year = 2014
),
split_days AS (
    SELECT FullName, Vendor, TransactionDate,
           COUNT(*)    AS SwipesThatDay,
           SUM(Amount) AS CombinedTotal
    FROM osu2014
    GROUP BY FullName, Vendor, TransactionDate
    HAVING COUNT(*) > 1 AND SUM(Amount) > 5000
)
SELECT o.Amount, o.FullName, o.Description, o.Vendor, o.TransactionDate,
       o.PostedDate, o.MCC,
       s.SwipesThatDay, ROUND(s.CombinedTotal, 2) AS CombinedTotal
FROM osu2014 o
JOIN split_days s
  ON s.FullName = o.FullName AND s.Vendor = o.Vendor AND s.TransactionDate = o.TransactionDate
ORDER BY o.TxDate ASC, o.FullName, o.Vendor, o.Amount DESC;


-- name: Question5
-- Control 5: purchases should not be split between two cardholders.
-- Vendor + day with exactly two different cardholders and exactly one charge
-- each (COUNT(*) = 2 removes cases where one person paid twice), total > $5,000.
WITH osu2014 AS (
    SELECT *,
           date(printf('%d-%02d-%02d', Year, Month,
                CAST(substr(TransactionDate, instr(TransactionDate, '/') + 1, 2) AS INTEGER))) AS TxDate
    FROM pcards
    WHERE AgencyNumber = 1000 AND Year = 2014
),
split_vendor_days AS (
    SELECT Vendor, TransactionDate, SUM(Amount) AS CombinedTotal
    FROM osu2014
    GROUP BY Vendor, TransactionDate
    HAVING COUNT(DISTINCT FullName) = 2
       AND COUNT(*) = 2
       AND SUM(Amount) > 5000
)
SELECT o.Amount, o.FullName, o.Description, o.Vendor, o.TransactionDate,
       o.PostedDate, o.MCC, ROUND(s.CombinedTotal, 2) AS CombinedTotal
FROM osu2014 o
JOIN split_vendor_days s
  ON s.Vendor = o.Vendor AND s.TransactionDate = o.TransactionDate
ORDER BY o.TxDate ASC, o.Vendor, o.FullName;


-- name: Question6
-- Control 6: purchases should not be split between two vendors.
-- Cardholder + day with exactly two different vendors and exactly one charge
-- at each (COUNT(*) = 2 removes double payments at one vendor), total > $5,000.
WITH osu2014 AS (
    SELECT *,
           date(printf('%d-%02d-%02d', Year, Month,
                CAST(substr(TransactionDate, instr(TransactionDate, '/') + 1, 2) AS INTEGER))) AS TxDate
    FROM pcards
    WHERE AgencyNumber = 1000 AND Year = 2014
),
split_person_days AS (
    SELECT FullName, TransactionDate, SUM(Amount) AS CombinedTotal
    FROM osu2014
    GROUP BY FullName, TransactionDate
    HAVING COUNT(DISTINCT Vendor) = 2
       AND COUNT(*) = 2
       AND SUM(Amount) > 5000
)
SELECT o.Amount, o.FullName, o.Description, o.Vendor, o.TransactionDate,
       o.PostedDate, o.MCC, ROUND(s.CombinedTotal, 2) AS CombinedTotal
FROM osu2014 o
JOIN split_person_days s
  ON s.FullName = o.FullName AND s.TransactionDate = o.TransactionDate
ORDER BY o.TxDate ASC, o.FullName, o.Vendor;


-- name: Question7
-- Control 7: no food/restaurant charges while in travel status.
-- Days on which the cardholder has a lodging charge (MCC contains hotel, motel,
-- resort or inn) -> return that cardholder's food/restaurant charges on the same day.
WITH osu2014 AS (
    SELECT * FROM pcards WHERE AgencyNumber = 1000 AND Year = 2014
),
lodging_days AS (
    SELECT DISTINCT FullName, TransactionDate
    FROM osu2014
    WHERE MCC LIKE '%hotel%' OR MCC LIKE '%motel%'
       OR MCC LIKE '%resort%' OR MCC LIKE '%inn%'
)
SELECT o.FullName, o.Amount, o.Description, o.Vendor, o.TransactionDate,
       o.PostedDate, o.MCC
FROM osu2014 o
JOIN lodging_days l
  ON l.FullName = o.FullName AND l.TransactionDate = o.TransactionDate
WHERE o.MCC LIKE '%food%' OR o.MCC LIKE '%restaurant%'
ORDER BY o.FullName ASC, o.Amount ASC;


-- name: Question8
-- Control 8 (student-defined): Alcohol is a prohibited P-Card purchase.
-- Charges at merchants whose MCC is a liquor/package store or a drinking place.
SELECT Amount, FullName, Description, Vendor, TransactionDate, PostedDate, MCC
FROM pcards
WHERE AgencyNumber = 1000
  AND Year = 2014
  AND (MCC LIKE '%LIQUOR%' OR MCC LIKE 'DRINKING PLACES%')
ORDER BY Amount DESC;


-- name: Question9
-- Control 9 (student-defined): Gasoline must be bought from Transportation
-- Services or with the vehicle fuel card, not with the P-Card.
-- Cardholders with fuel-merchant charges, ranked by total.
SELECT FullName,
       COUNT(*)              AS FuelTransactions,
       ROUND(SUM(Amount), 2) AS FuelTotal,
       ROUND(MAX(Amount), 2) AS LargestFuelCharge
FROM pcards
WHERE AgencyNumber = 1000
  AND Year = 2014
  AND (MCC LIKE 'SERVICE STATIONS%' OR MCC = 'AUTOMATED FUEL DISPENSER')
GROUP BY FullName
ORDER BY FuelTotal DESC;


-- name: Question10
-- Control 10 (student-defined): Goods/services from University departments and
-- auxiliaries must go through the Campus Vendor Invoice (CVI) system.
-- Charges at vendors that are OSU units, by vendor and cardholder.
SELECT Vendor, FullName,
       COUNT(*)              AS Transactions,
       ROUND(SUM(Amount), 2) AS Total
FROM pcards
WHERE AgencyNumber = 1000
  AND Year = 2014
  AND (Vendor LIKE 'OSU %' OR Vendor LIKE 'OSU-%' OR Vendor LIKE '%OKLAHOMA STATE UNIV%')
GROUP BY Vendor, FullName
ORDER BY Total DESC;


-- name: Question11
-- Control 11 (student-defined): U.S. mail and postage must go through
-- University Mailing, not the P-Card.
SELECT Amount, FullName, Description, Vendor, TransactionDate, PostedDate, MCC
FROM pcards
WHERE AgencyNumber = 1000
  AND Year = 2014
  AND (MCC = 'POSTAGE STAMPS' OR Vendor LIKE 'USPS%')
ORDER BY Amount DESC;


-- name: Question12
-- Control 12 (student-defined): Regular monthly payments (maintenance, lease,
-- rental, service agreements) above $5,000 a year must be on a requisition/PO.
-- Same cardholder paying the same vendor in at least 6 different months of 2014
-- with a combined total above $5,000, limited to merchant categories that
-- represent maintenance, lease/rental or ongoing service agreements (or a
-- description that mentions lease, rent, maintenance or a service contract).
-- Restricting categories avoids flagging ordinary repeat supply purchases.
SELECT FullName, Vendor, MCC,
       COUNT(DISTINCT Month)  AS MonthsWithCharges,
       COUNT(*)               AS Transactions,
       ROUND(SUM(Amount), 2)  AS Total2014
FROM pcards
WHERE AgencyNumber = 1000
  AND Year = 2014
  AND (   MCC IN ('EQUIP, TOOL, FURNITURE, AND APPLIANCE RENTAL AND LEASING',
                  'TRUCK AND UTILITY TRAILER RENTALS',
                  'OFFICE, PHOTOGRAPHIC, PHOTOCOPY, AND MICROFILM EQUIPMENT',
                  'TELECOMMUNICATION SERVICES',
                  'CABLE, SATELLITE, AND OTHER PAY TELEVISION AND RADIO SERVICES',
                  'UTILITIES - ELECTRIC,GAS,WATER,SANITARY',
                  'COMPUTER NETWORK/INFORMATION SERVICES',
                  'CONTINUITY/SUBSCRIPTION MERCHANTS',
                  'EXTERMINATING AND DISINFECTING SERVICES',
                  'CLEANING AND MAINTENANCE,JANITORIAL SERVICES',
                  'DETECTIVE AGENCIES,PROTECTIVE AGENCIES,AND SECURITY SERVICES',
                  'COMPUTER MAINTENANCE, REPAIR AND SERVICES NOT ELSEWHERE CLAS',
                  'ELECTRICAL AND SMALL APPLIANCE REPAIR SHOPS',
                  'MISCELLANEOUS REPAIR SHOPS AND RELATED SERVICES',
                  'AIR CONDITIONING AND REFRIGERATION REPAIR SHOPS',
                  'PUBLIC WAREHOUSING',
                  'LANDSCAPING AND HORTICULTURAL SERVICES')
       OR Description LIKE '%LEASE%' OR Description LIKE '%RENTAL%'
       OR Description LIKE '%MAINT%' OR Description LIKE '%SERVICE AGREEMENT%'
       OR Description LIKE '%SERVICE CONTRACT%')
GROUP BY FullName, Vendor, MCC
HAVING COUNT(DISTINCT Month) >= 6
   AND SUM(Amount) > 5000
ORDER BY Total2014 DESC;


-- name: Question13
-- Control 13 (student-defined): Oklahoma sales tax must not be paid.
-- Line descriptions that show sales tax being charged or later corrected.
SELECT Amount, FullName, Description, Vendor, TransactionDate, PostedDate, MCC
FROM pcards
WHERE AgencyNumber = 1000
  AND Year = 2014
  AND (   Description LIKE '%SALES TAX%'
       OR Description LIKE '%TAX CORRECTION%'
       OR Description LIKE '%TAX ADJUST%'
       OR Description LIKE '%TAX REFUND%'
       OR Description LIKE '%TAXREFUND%'
       OR Description LIKE 'TAX %')
ORDER BY Vendor, Amount;


-- name: Question14
-- Control 14 (student-defined): Donations and sponsorships are prohibited.
-- Religious or political organisations, plus charitable-organisation merchants
-- whose names indicate a foundation or charity (filters out the many
-- professional associations that share the 'charitable' MCC), plus any
-- description/vendor mentioning a donation or sponsorship.
SELECT Amount, FullName, Description, Vendor, TransactionDate, PostedDate, MCC
FROM pcards
WHERE AgencyNumber = 1000
  AND Year = 2014
  AND (   MCC LIKE 'RELIGIOUS ORGANIZATIONS%'
       OR MCC LIKE 'POLITICAL ORGANIZATIONS%'
       OR (MCC LIKE 'CHARITABLE%' AND (   Vendor LIKE '%FOUNDATION%' OR Vendor LIKE '%FDN%'
                                       OR Vendor LIKE '%RED CROSS%'   OR Vendor LIKE '%CANCER%'
                                       OR Vendor LIKE '%MARCH OF DIMES%' OR Vendor LIKE '%CHARIT%'
                                       OR Vendor LIKE '%UNITED WAY%'))
       OR Description LIKE '%DONATION%' OR Vendor LIKE '%DONATION%'
       OR Description LIKE '%SPONSOR%'  OR Vendor LIKE '%SPONSOR%')
  AND Description NOT LIKE '%Donation of Constantin%'   -- a book title, not a donation
ORDER BY Amount DESC;


-- =====================================================================
-- PART III - FORENSIC / FRAUD TESTS
-- =====================================================================

-- name: qry_T3_Question1
-- Benford's law, first digit of amounts >= $1.00, 2014 OSU transactions.
-- (Amounts >= 1 never start with '0' or '-', so the first character is the first digit.)
WITH digits AS (
    SELECT CAST(substr(CAST(Amount AS TEXT), 1, 1) AS INTEGER) AS FirstDigit
    FROM pcards
    WHERE AgencyNumber = 1000
      AND Year = 2014
      AND Amount >= 1
),
-- Expected Benford % = 100 * log10(1 + 1/d), written as constants so the query
-- also runs in SQLite builds without math functions.
benford(FirstDigit, BenfordPct) AS (
    VALUES (1, 30.103), (2, 17.609), (3, 12.494), (4, 9.691), (5, 7.918),
           (6, 6.695), (7, 5.799), (8, 5.115), (9, 4.576)
)
SELECT d.FirstDigit,
       COUNT(*)                                                         AS Transactions,
       ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM digits), 3)       AS ActualPct,
       b.BenfordPct,
       ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM digits) - b.BenfordPct, 3) AS DifferencePctPts
FROM digits d
JOIN benford b ON b.FirstDigit = d.FirstDigit
GROUP BY d.FirstDigit, b.BenfordPct
ORDER BY d.FirstDigit;


-- name: qry_T3_Question2
-- Possible duplicate payments: positive charges with the same date, vendor,
-- first initial, last name and amount (all members of each group returned).
WITH osu2014 AS (
    SELECT *,
           date(printf('%d-%02d-%02d', Year, Month,
                CAST(substr(TransactionDate, instr(TransactionDate, '/') + 1, 2) AS INTEGER))) AS TxDate
    FROM pcards
    WHERE AgencyNumber = 1000 AND Year = 2014 AND Amount > 0
),
dup_groups AS (
    SELECT TransactionDate, Vendor, CardholderFirstInitial, CardholderLastName, Amount
    FROM osu2014
    GROUP BY TransactionDate, Vendor, CardholderFirstInitial, CardholderLastName, Amount
    HAVING COUNT(*) > 1
)
SELECT o.TransactionDate, o.Vendor, o.FullName, o.Amount
FROM osu2014 o
JOIN dup_groups d
  ON  d.TransactionDate        = o.TransactionDate
  AND d.Vendor                 = o.Vendor
  AND d.CardholderFirstInitial = o.CardholderFirstInitial
  AND d.CardholderLastName     = o.CardholderLastName
  AND d.Amount                 = o.Amount
ORDER BY o.TxDate ASC, o.Vendor ASC, o.FullName, o.Amount;


-- name: qry_T3_Question3
-- Employees repeatedly associated with possible duplicates: number of separate
-- duplicate occasions (date + vendor + amount groups), not number of rows.
WITH dup_groups AS (
    SELECT TransactionDate, Vendor, CardholderFirstInitial, CardholderLastName, Amount,
           MIN(FullName) AS FullName,
           COUNT(*)      AS ChargesInGroup
    FROM pcards
    WHERE AgencyNumber = 1000 AND Year = 2014 AND Amount > 0
    GROUP BY TransactionDate, Vendor, CardholderFirstInitial, CardholderLastName, Amount
    HAVING COUNT(*) > 1
)
SELECT FullName,
       COUNT(*)                                       AS DuplicateOccasions,
       SUM(ChargesInGroup)                            AS ChargesInvolved,
       ROUND(SUM(Amount * (ChargesInGroup - 1)), 2)   AS PotentialOverpayment
FROM dup_groups
GROUP BY FullName
ORDER BY DuplicateOccasions DESC, PotentialOverpayment DESC;


-- name: qry_T3_Question4
-- Four-digit whole-dollar amounts ending in 000 (1,000 ... 9,000), cents ignored.
SELECT Amount, Vendor, Description, FullName
FROM pcards
WHERE AgencyNumber = 1000
  AND Year = 2014
  AND Amount > 0
  AND CAST(Amount AS INTEGER) BETWEEN 1000 AND 9999
  AND CAST(Amount AS INTEGER) % 1000 = 0
ORDER BY Vendor ASC, FullName ASC;


-- name: qry_T3_Question5
-- Student-defined fraud test 1: limit-avoidance clustering just below $5,000.
-- Cardholders with charges between $4,500.00 and $4,999.99 (within 10% of the
-- single-transaction limit), ranked by how often they land there.
SELECT FullName,
       COUNT(*)                         AS ChargesJustBelowLimit,
       COUNT(DISTINCT Vendor)           AS Vendors,
       ROUND(SUM(Amount), 2)            AS Total,
       GROUP_CONCAT(DISTINCT Vendor)    AS VendorList
FROM pcards
WHERE AgencyNumber = 1000
  AND Year = 2014
  AND Amount BETWEEN 4500 AND 4999.99
GROUP BY FullName
HAVING COUNT(*) >= 2
ORDER BY ChargesJustBelowLimit DESC, Total DESC;


-- name: qry_T3_Question6
-- Student-defined fraud test 2: retail purchases on weekends and US federal
-- holidays (personal-use risk). Travel, lodging, food, transport and
-- membership/registration categories are excluded because staff legitimately
-- travel and attend events on weekends. Ranked by cardholder spend.
WITH osu2014 AS (
    SELECT *,
           date(printf('%d-%02d-%02d', Year, Month,
                CAST(substr(TransactionDate, instr(TransactionDate, '/') + 1, 2) AS INTEGER))) AS TxDate
    FROM pcards
    WHERE AgencyNumber = 1000 AND Year = 2014 AND Amount > 0
),
off_days AS (
    SELECT *,
           CASE WHEN TxDate IN ('2014-01-01','2014-01-20','2014-02-17','2014-05-26',
                                '2014-07-04','2014-09-01','2014-10-13','2014-11-11',
                                '2014-11-27','2014-12-25')
                THEN 'Federal holiday'
                WHEN strftime('%w', TxDate) = '0' THEN 'Sunday'
                ELSE 'Saturday' END AS DayType
    FROM osu2014
    WHERE (strftime('%w', TxDate) IN ('0', '6')
           OR TxDate IN ('2014-01-01','2014-01-20','2014-02-17','2014-05-26',
                         '2014-07-04','2014-09-01','2014-10-13','2014-11-11',
                         '2014-11-27','2014-12-25'))
      AND MCC IN ('GROCERY STORES,AND SUPERMARKETS', 'DISCOUNT STORES', 'DEPARTMENT STORES',
                  'VARIETY STORES', 'ELECTRONICS STORES', 'HOME SUPPLY WAREHOUSE STORES',
                  'FAMILY CLOTHING STORES', 'MISCELLANEOUS APPAREL AND ACCESSORY STORES',
                  'SHOE STORES', 'SPORTING GOODS STORES', 'HOBBY,TOY,AND GAME STORES',
                  'COSMETIC STORES', 'JEWELRY,WATCH,CLOCK,AND SILVERWARE STORES',
                  'HOUSEHOLD APPLIANCE STORES', 'FURNITURE,HOME FURNISHINGS AND EQUIPMENT STORES',
                  'WOMEN''S READY-TO-WEAR STORES', 'MEN''S AND WOMEN''S CLOTHING STORES',
                  'DRUG STORES AND PHARMACIES', 'WHOLESALE CLUBS', 'RECORD STORES',
                  'DVD/VIDEO TAPE RENTAL STORES', 'MOTION PICTURE THEATERS',
                  'GIFT,CARD,NOVELTY,AND SOUVENIR STORES')
)
SELECT FullName,
       COUNT(*)                                        AS OffDayRetailCharges,
       SUM(DayType = 'Federal holiday')                AS OnHolidays,
       ROUND(SUM(Amount), 2)                           AS OffDayRetailTotal,
       ROUND(MAX(Amount), 2)                           AS LargestCharge,
       GROUP_CONCAT(DISTINCT MCC)                      AS Categories
FROM off_days
GROUP BY FullName
HAVING COUNT(*) >= 5 OR SUM(Amount) >= 1000
ORDER BY OffDayRetailTotal DESC;


-- name: qry_T3_Question7
-- Student-defined fraud test 3: vendors paid by only ONE cardholder in the whole
-- university with high annual spend (risk of a fictitious vendor, kickback or an
-- undisclosed related-party/conflict-of-interest supplier).
WITH vendor_stats AS (
    SELECT Vendor,
           COUNT(DISTINCT FullName) AS Cardholders,
           COUNT(*)                 AS Transactions,
           SUM(Amount)              AS Total
    FROM pcards
    WHERE AgencyNumber = 1000 AND Year = 2014
    GROUP BY Vendor
)
SELECT v.Vendor,
       (SELECT MIN(p.FullName) FROM pcards p
         WHERE p.AgencyNumber = 1000 AND p.Year = 2014 AND p.Vendor = v.Vendor) AS OnlyCardholder,
       (SELECT MIN(p.MCC) FROM pcards p
         WHERE p.AgencyNumber = 1000 AND p.Year = 2014 AND p.Vendor = v.Vendor) AS MCC,
       v.Transactions,
       ROUND(v.Total, 2)                AS Total2014,
       ROUND(v.Total / v.Transactions, 2) AS AvgCharge
FROM vendor_stats v
WHERE v.Cardholders = 1
  AND v.Total > 25000
ORDER BY v.Total DESC;


-- name: qry_T3_Question8
-- Student-defined fraud test 4: merchant categories that are personal in nature
-- (beauty, cosmetics, spas, jewellery, pawn shops, arcades, video rental,
-- personal counselling) and almost never serve a university business purpose.
SELECT Amount, FullName, Description, Vendor, TransactionDate, MCC
FROM pcards
WHERE AgencyNumber = 1000
  AND Year = 2014
  AND Amount > 0
  AND MCC IN ('COSMETIC STORES', 'BEAUTY AND BARBER SHOPS', 'HEALTH AND BEAUTY SPAS',
              'JEWELRY,WATCH,CLOCK,AND SILVERWARE STORES',
              'PRECIOUS STONES AND METALS, WATCHES & JEWELRY',
              'PAWN SHOPS', 'VIDEO GAME ARCADES AND ESTABLISHMENTS',
              'DVD/VIDEO TAPE RENTAL STORES',
              'COUNSELING SERVICES--DEBT,MARRIAGE,PERSONAL')
ORDER BY MCC, FullName, Amount DESC;
