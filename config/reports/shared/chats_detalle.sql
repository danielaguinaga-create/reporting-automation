SELECT DISTINCT
    c.idRoom                        AS RoomID,
    c.ChatSentAtUTC,
    u.UserToken,
    u.UserFirstName,
    u.UserLastName,
    s.SpecialityES,
    c.ChatSentBy,
    c.ChatCharactersProfessional,
    c.ChatMessagesProfessional,
    c.ChatCharactersUser,
    c.ChatMessagesUser
FROM `data-prd-424213.03_BaseModel.FactChatConsultations` AS c
JOIN `data-prd-424213.03_BaseModel.DimUsers` AS u
    ON c.idUser = u.idUser
JOIN `data-prd-424213.03_BaseModel.DimSpecialities` AS s
    ON s.idSpeciality = c.idSpeciality
WHERE c.idCompany = @id_company
ORDER BY c.ChatSentAtUTC DESC;
