SELECT
    idUserMD                        AS UserID,
    UserHash,
    UserToken,
    UserFirstName,
    UserLastName,
    UserEmail,
    UserBirthdate,
    UserPhoneNumber,
    UserGenderNum,
    UserContractNumber,
    UserNidNumber,
    UserCoverageName,
    UserCustomerGroup,
    UserCompanyGroupCode,
    UserType,
    UserStatus,
    UserInvitationBy,
    CAST(UserFirstAccessedAtUTC AS STRING)  AS UserFirstAccessedAtUTC,
    CAST(UserSubscribedAtUTC AS STRING)     AS UserSubscribedAtUTC,
    CAST(UserUnsubscribedAtUTC AS STRING)   AS UserUnsubscribedAtUTC,
    CAST(UserUninstalledAtUTC AS STRING)    AS UserUninstalledAtUTC
FROM `data-prd-424213.03_BaseModel.DimUsers`
WHERE idCompany = @id_company
AND DATE_TRUNC(DATE(UserFirstAccessedAtUTC), MONTH)
    <= DATE_TRUNC(@target_month_date, MONTH);
