-- 1. Overall volume and approval rate
SELECT COUNT(*) AS requests,
       ROUND(AVG(APPROVED) * 100, 2) AS approval_rate_pct
FROM requests;

-- 2. Exact duplicate requests (same resource, manager and role on every field)
SELECT COUNT(*) AS duplicate_rows FROM (
  SELECT COUNT(*) AS n
  FROM requests
  GROUP BY RESOURCE, MGR_ID, ROLE_ROLLUP_1, ROLE_ROLLUP_2, ROLE_DEPTNAME,
           ROLE_TITLE, ROLE_FAMILY_DESC, ROLE_FAMILY, ROLE_CODE, APPROVED
  HAVING n > 1
);

-- 3. Departments with the lowest approval rates (min 200 requests)
SELECT ROLE_DEPTNAME,
       COUNT(*) AS requests,
       ROUND(AVG(APPROVED) * 100, 1) AS approval_rate_pct
FROM requests
GROUP BY ROLE_DEPTNAME
HAVING COUNT(*) >= 200
ORDER BY approval_rate_pct ASC
LIMIT 10;

-- 4. Peer-group outliers: requests for a resource that nobody else in the same
--    department has requested, while 5+ requests for it come from other departments
WITH dept_pair AS (
  SELECT ROLE_DEPTNAME, RESOURCE, COUNT(*) AS dept_n
  FROM requests GROUP BY ROLE_DEPTNAME, RESOURCE
),
res AS (
  SELECT RESOURCE, COUNT(*) AS res_n FROM requests GROUP BY RESOURCE
)
SELECT CASE WHEN dp.dept_n = 1 AND res.res_n - dp.dept_n >= 5
            THEN 'Outlier' ELSE 'Normal' END AS peer_group_status,
       COUNT(*) AS requests,
       ROUND((1 - AVG(r.APPROVED)) * 100, 1) AS denial_rate_pct
FROM requests r
JOIN dept_pair dp ON r.ROLE_DEPTNAME = dp.ROLE_DEPTNAME AND r.RESOURCE = dp.RESOURCE
JOIN res ON r.RESOURCE = res.RESOURCE
GROUP BY peer_group_status;

-- 5. Most-requested resources and how often they are denied
SELECT RESOURCE,
       COUNT(*) AS requests,
       ROUND((1 - AVG(APPROVED)) * 100, 1) AS denial_rate_pct
FROM requests
GROUP BY RESOURCE
ORDER BY requests DESC
LIMIT 10;
