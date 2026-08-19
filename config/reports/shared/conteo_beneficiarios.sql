SELECT
    UserType,
    COUNT(*) AS BeneficiariosActivos
FROM `data-prd-424213.03_BaseModel.DimUsers`
WHERE idCompany = @id_company
  AND UserStatus = 2
GROUP BY UserType
ORDER BY UserType;