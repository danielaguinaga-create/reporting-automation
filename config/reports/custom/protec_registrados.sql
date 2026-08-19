SELECT
    UserToken,
    UserFirstAccessedAtUTC AS RegisteredAtUTC
FROM `data-prd-424213.03_BaseModel.DimUsers`
WHERE idCompany = '498cb81c5ba7325f'
ORDER BY UserFirstAccessedAtUTC DESC;
