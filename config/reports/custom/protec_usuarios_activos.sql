SELECT DISTINCT UserToken, UserSubscribedAtUTC
FROM `data-prd-424213.03_BaseModel.DimUsers`
WHERE idCompany = '498cb81c5ba7325f'
AND UserStatus = 2
ORDER BY UserSubscribedAtUTC DESC;
