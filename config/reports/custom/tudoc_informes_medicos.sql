SELECT
    u.UserToken,
    u.UserFirstName,
    u.UserLastName,
    mr.MedicalRepIcdID,
    mr.MedicalRepIcdName,
    mr.MedicalRepRecommendations,
    mr.MedicalRepDateAtUTC AS ConsultationDateAtUTC
FROM `data-prd-424213.03_BaseModel.FactMedicalReports` AS mr
LEFT JOIN `data-prd-424213.03_BaseModel.DimUsers` AS u
    ON mr.idUser = u.idUser
WHERE mr.idCompany = 'jmUCkN9AqnY25RJS'
AND mr.MedicalRepUserCompanyGroupCode = 'TuDoc_Innocean'
ORDER BY mr.MedicalRepDateAtUTC DESC;
