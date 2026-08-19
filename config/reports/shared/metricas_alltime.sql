WITH Chats AS (
  SELECT
    idUser,
    COUNT(*) AS TotalChats
  FROM `data-prd-424213.03_BaseModel.FactChatConsultations`
  GROUP BY idUser
),
Videocalls AS (
  SELECT
    idUser,
    COUNT(*) AS TotalVideocalls
  FROM `data-prd-424213.03_BaseModel.FactVideoCalls`
  WHERE VcStatus = 'finished'
  GROUP BY idUser
),
Installations AS (
  SELECT
    UserHash,
    COUNT(*) AS TotalInstallations
  FROM `data-prd-424213.03_BaseModel.FactInstallations`
  GROUP BY UserHash
)

SELECT
  du.UserToken,
  du.UserCustomerGroup AS Description,
  COALESCE(c.TotalChats, 0) AS Chats,
  COALESCE(v.TotalVideocalls, 0) AS Videocalls,
  COALESCE(i.TotalInstallations, 0) AS Installations
FROM `data-prd-424213.03_BaseModel.DimUsers` du
LEFT JOIN Chats c
  ON du.idUser = c.idUser
LEFT JOIN Videocalls v
  ON du.idUser = v.idUser
LEFT JOIN Installations i
  ON du.UserHash = i.UserHash
WHERE du.idCompany = @id_company
ORDER BY du.UserToken;
