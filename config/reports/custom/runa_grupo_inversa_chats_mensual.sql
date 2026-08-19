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
        = DATE_TRUNC(DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH), MONTH)
) AS c
    ON c.idUser = u.idUser
WHERE u.UserCompanyGroupCode IN (
    'Runa_Basico',
    'Runa_Basico_Ind',
    'Runa_Completo',
    'Runa_Completo_Ind'
)
AND u.UserCoverageName = 'Runa - Grupo Inversa'
GROUP BY
    u.UserToken,
    u.UserFirstName,
    u.UserLastName,
    u.UserCoverageName
ORDER BY chat_count DESC;