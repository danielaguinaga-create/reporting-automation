SELECT
    UserType,
    UserCompanyGroupCode,
    COUNT(*) AS Beneficiarios
FROM `data-prd-424213.03_BaseModel.DimUsers`
WHERE idCompany = @id_company
AND (
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
AND (UserEmail IS NULL OR UserEmail NOT LIKE '%@meetingdoctors.com%')
GROUP BY UserType, UserCompanyGroupCode
ORDER BY UserType, UserCompanyGroupCode;
