WITH users_agg AS (
    SELECT
        idCompany               AS api_key,
        UserCompanyGroupCode    AS company_group_code,
        UserType                AS user_type,
        COUNT(idUser)             AS users_count
    FROM `data-prd-424213.03_BaseModel.DimUsers`
    WHERE (
        DATE(UserUnsubscribedAtUTC) BETWEEN @start_date AND @end_date

        OR

        DATE(UserSubscribedAtUTC) BETWEEN @start_date AND @end_date

        OR

        (
            UserStatus = 2
            AND DATE(UserSubscribedAtUTC) <= @end_date
        )

        OR

        (
            UserStatus = 2
            AND UserSubscribedAtUTC IS NULL
        )
    )
    AND (UserEmail IS NULL OR UserEmail NOT LIKE '%@meetingdoctors.com')
    GROUP BY idCompany, UserCompanyGroupCode, UserType
),
companies AS (
    SELECT DISTINCT
        CompanyName,
        idCompany
    FROM `data-prd-424213.03_BaseModel.DimCompanies`
    WHERE CompanyName IN (
        SELECT TRIM(name) FROM UNNEST(SPLIT(@company_names, ',')) AS name
    )
)
SELECT
    comp.CompanyName,
    u.user_type,
    SUM(u.users_count) AS total_users
FROM users_agg AS u
JOIN companies AS comp
    ON u.api_key = comp.idCompany
GROUP BY comp.CompanyName, u.user_type
ORDER BY comp.CompanyName, u.user_type;
