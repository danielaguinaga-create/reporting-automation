SELECT DISTINCT
    s.SpecialityES,
    n.idNPS                        AS NpsID,
    n.NpsScore,
    n.NpsScoreGroup,
    n.NpsSatisfaction,
    n.NpsSatisfactionGroup,
    n.NpsDateAtUTC,
    n.NpsSentAtUTC,
    n.NpsRatedAtUTC,
    n.NpsUserCompanyGroupCode,
    n.NpsUserCoverageName,
    n.NpsUserCustomerGroup,
    n.NpsUserType,
    n.NpsUserContractNumber,
    n.NpsUserStatus,
    n.NpsUserGender,
    n.MeetingProsType,
    n.NpsSendingMethod
FROM `data-prd-424213.03_BaseModel.FactNPSResponse` AS n
JOIN `data-prd-424213.03_BaseModel.DimSpecialities` AS s
    ON s.idSpeciality = n.idSpeciality
WHERE n.idCompany = @id_company
ORDER BY n.NpsRatedAtUTC ASC;
