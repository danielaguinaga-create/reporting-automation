SELECT
    UserToken,
    UserStatus,
    UserFirstAccessedAtUTC AS FechaRegistro,
    CASE
        WHEN UserFirstAccessedAtUTC IS NULL THEN 'No'
        ELSE 'Si'
    END AS Registrado
FROM `data-prd-424213.03_BaseModel.DimUsers`
WHERE idCompany = @id_company
AND UserStatus = 2;