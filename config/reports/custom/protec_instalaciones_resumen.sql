SELECT
    du.UserToken,
    COUNT(fi.idInstall) AS Installations
FROM `data-prd-424213.03_BaseModel.DimUsers` AS du
LEFT JOIN `data-prd-424213.03_BaseModel.FactInstallations` AS fi
    ON du.UserHash = fi.UserHash
WHERE du.idCompany = '498cb81c5ba7325f'
GROUP BY du.UserToken
ORDER BY Installations DESC;
