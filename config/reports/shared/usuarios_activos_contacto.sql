SELECT
    u.UserToken           AS token,
    u.UserFirstName        AS firstname,
    u.UserLastName          AS lastname,
    u.UserNidNumber          AS nid_number,
    CAST(u.UserPhoneNumber AS STRING)  AS mobile_phone,
    u.UserEmail             AS email,
    u.UserGenderNum          AS gender,
    u.UserBirthdate          AS birthdate,
    u.UserCoverageName       AS coverage_name,
    u.UserContractNumber     AS contract_number,
    u.UserCustomerGroup      AS description,
    u.UserCompanyGroupCode   AS company_group_code
FROM `data-prd-424213.03_BaseModel.DimUsers` AS u
WHERE u.UserStatus = 2
AND u.idCompany = @id_company;
