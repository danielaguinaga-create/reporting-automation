SELECT DISTINCT
    u.UserCompanyGroupCode,
    COUNT(*) AS total
FROM `data-prd-424213.03_BaseModel.FactChatConsultations` AS c
JOIN `data-prd-424213.03_BaseModel.DimUsers` AS u
    ON u.idUser = c.idUser
WHERE c.idCompany = @id_company
AND DATE_TRUNC(DATE(c.ChatSentAtUTC), MONTH)
    <= DATE_TRUNC(DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH), MONTH)
GROUP BY u.UserCompanyGroupCode
ORDER BY u.UserCompanyGroupCode;