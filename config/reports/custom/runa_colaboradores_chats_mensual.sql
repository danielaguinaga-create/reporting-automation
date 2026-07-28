SELECT
    u.UserToken,
    u.UserFirstName,
    u.UserLastName,
    u.UserCoverageName,
    COUNT(*) AS chat_count
FROM `data-prd-424213.03_BaseModel.DimUsers` AS u
JOIN (
    SELECT DISTINCT
        idUser,
        ChatSentAtUTC
    FROM `data-prd-424213.03_BaseModel.FactChatConsultations`
    WHERE ChatSentAtUTC IS NOT NULL
    AND DATE_TRUNC(DATE(ChatSentAtUTC), MONTH)
        = DATE_TRUNC(@billing_month_date, MONTH)
) AS c
    ON c.idUser = u.idUser
WHERE (
    LOWER(u.UserCompanyGroupCode) LIKE 'runa%'
    OR LOWER(u.UserCoverageName) LIKE 'runa%'
)
AND LOWER(u.UserToken) NOT LIKE '%@inversainstalaciones%'
AND LOWER(u.UserToken) NOT LIKE '%@jova%'
GROUP BY
    u.UserToken,
    u.UserFirstName,
    u.UserLastName,
    u.UserCoverageName
ORDER BY chat_count DESC;
