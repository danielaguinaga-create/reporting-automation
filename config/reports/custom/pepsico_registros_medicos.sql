SELECT DISTINCT
    DATE_TRUNC(DATE(mr.MedicalRepDateAtUTC), DAY) AS MedicalRepDateAtUTC,
    mr.MedicalRepMotivation AS Motivation,
    mr.MedicalRepAnamnesis AS Anamnesis,
    mr.MedicalRepOrientation AS Orientation,
    mr.MedicalRepRecommendations AS Recommendation,
    mr.MedicalRepIcdName AS Diagnosis,
    u.UserGender,
    u.UserContractNumber,
    u.UserCoverageName
FROM `data-prd-424213.03_BaseModel.FactMedicalReports` AS mr
JOIN `data-prd-424213.03_BaseModel.DimUsers` AS u
    ON u.idUser = mr.idUser
WHERE u.UserCompanyGroupCode = 'pepsico_vulnerables'
AND u.UserStatus = 2
ORDER BY MedicalRepDateAtUTC DESC;