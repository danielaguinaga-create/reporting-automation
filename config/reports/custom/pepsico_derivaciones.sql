SELECT DISTINCT
    r.ReferralDateAtUTC,
    s.SpecialityES,
    p.ProfessionalName AS PROFESSIONALFULLNAME,
    r.ReferralDetail,
    r.ReferralReason,
    u.UserToken,
    u.UserCompanyGroupCode,
    u.UserCoverageName,
    u.UserCustomerGroup,
    u.UserContractNumber
FROM `data-prd-424213.03_BaseModel.FactReferrals` AS r
JOIN `data-prd-424213.03_BaseModel.DimUsers` AS u
    ON u.UserHash = r.UserHash
JOIN `data-prd-424213.03_BaseModel.DimSpecialities` AS s
    ON s.idSpeciality = r.idSpeciality
JOIN `data-prd-424213.03_BaseModel.DimProfessionals` AS p
    ON p.idProfessional = r.idProfessional
WHERE u.idCompany = 'eeadbae0aa64f0ea'
AND (
    u.UserCompanyGroupCode = 'default-pepsico'
    OR u.UserCompanyGroupCode IS NULL
)
AND r.ReferralDateAtUTC >= '2024-01-01 00:00:00'
AND u.UserType = 'primary'
AND u.UserToken NOT IN ('46946201G', 'X9444250J')
ORDER BY r.ReferralDateAtUTC DESC;