SELECT
    u.UserToken,
    r.RegistrationDate,
    u.UserStatus
FROM `data-prd-424213.03_BaseModel.FactRegistrations` AS r
LEFT JOIN `data-prd-424213.03_BaseModel.DimUsers` AS u
    ON r.idUser = u.idUser
    AND r.idCompany = u.idCompany
WHERE r.idCompany = '77ea8d28201a947d'
AND (
    u.UserEmail IS NULL
    OR LOWER(u.UserEmail) NOT LIKE '%@meeting%'
)
AND (
    u.UserToken IS NULL
    OR (
        u.UserToken NOT LIKE 'AG%'
        AND u.UserToken NOT LIKE 'PROM%'
    )
)
ORDER BY r.RegistrationDate ASC;