SELECT
    UserCompanyGroupCode,
    UserContractNumber,
    UserNidNumber,
    UserCoverageName,
    UserCustomerGroup,
    UserType,
    UserStatus,
    UserToken,
    UserFirstName,
    UserLastName,
    CAST(UserSubscribedAtUTC AS STRING)     AS UserSubscribedAtUTC,
    CAST(UserFirstAccessedAtUTC AS STRING)  AS UserFirstAccessedAtUTC,
    CAST(UserUnsubscribedAtUTC AS STRING)   AS UserUnsubscribedAtUTC
FROM `data-prd-424213.03_BaseModel.DimUsers` AS u
WHERE idCompany = @id_company
AND (u.UserToken NOT LIKE '%@meetingdoctors.com%')
AND (
    DATE(u.UserUnsubscribedAtUTC) BETWEEN @start_date AND @end_date

    OR

    DATE(u.UserSubscribedAtUTC) BETWEEN @start_date AND @end_date

    OR

    (
        u.UserStatus = 2
        AND DATE(u.UserSubscribedAtUTC) <= @end_date
    )

    OR

    (
        u.UserStatus = 2
        AND u.UserSubscribedAtUTC IS NULL
    )
);