SELECT DISTINCT
    u.UserToken,
    i.*
FROM `data-prd-424213.03_BaseModel.FactInstallations` AS i
LEFT JOIN `data-prd-424213.03_BaseModel.DimUsers` AS u
    ON u.UserHash = i.UserHash
WHERE i.idCompany = @id_company;
