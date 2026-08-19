WITH usuarios_filtrados AS (
    SELECT DISTINCT
        u.idUser,
        u.idCompany,
        CASE
            WHEN TRIM(u.UserContractNumber) LIKE '%0191101%' THEN '0191101'
            WHEN TRIM(u.UserContractNumber) LIKE '%0191401%' THEN '0191401'
            WHEN TRIM(u.UserContractNumber) LIKE '%0190901%' THEN '0190901'
            WHEN TRIM(u.UserContractNumber) LIKE '%0191001%' THEN '0191001'
            WHEN TRIM(u.UserContractNumber) LIKE '%0195101%' THEN '0195101'
        END AS GrupoContrato
    FROM `data-prd-424213.03_BaseModel.DimUsers` AS u
    WHERE u.idCompany = '77ea8d28201a947d'
      AND u.UserStatus = 2
      AND u.UserToken IS NOT NULL
      AND TRIM(u.UserToken) != ''
      AND TRIM(u.UserToken) NOT LIKE 'AG%'
      AND TRIM(u.UserToken) NOT LIKE 'PROM%'
      AND (
            TRIM(u.UserContractNumber) LIKE '%0191101%'
         OR TRIM(u.UserContractNumber) LIKE '%0191401%'
         OR TRIM(u.UserContractNumber) LIKE '%0190901%'
         OR TRIM(u.UserContractNumber) LIKE '%0191001%'
         OR TRIM(u.UserContractNumber) LIKE '%0195101%'
      )
),

chats_totales AS (
    SELECT
        uf.GrupoContrato,
        COUNT(*) AS Chats
    FROM usuarios_filtrados AS uf
    JOIN `data-prd-424213.03_BaseModel.FactChatConsultations` AS c
        ON uf.idUser = c.idUser
       AND uf.idCompany = c.idCompany
    GROUP BY uf.GrupoContrato
),

videollamadas_totales AS (
    SELECT
        uf.GrupoContrato,
        COUNT(DISTINCT v.idVc) AS Videollamadas
    FROM usuarios_filtrados AS uf
    JOIN `data-prd-424213.03_BaseModel.FactVideoCalls` AS v
        ON uf.idUser = v.idUser
       AND uf.idCompany = v.idCompany
    WHERE v.VcStatus = 'finished'
    GROUP BY uf.GrupoContrato
),

especialidades AS (
    SELECT
        x.GrupoContrato,
        STRING_AGG(
            DISTINCT x.Especialidad,
            ', '
            ORDER BY x.Especialidad
        ) AS Especialidades
    FROM (
        SELECT
            uf.GrupoContrato,
            COALESCE(
                s.SpecialityES,
                'Consulta sin especialidad'
            ) AS Especialidad
        FROM usuarios_filtrados AS uf
        JOIN `data-prd-424213.03_BaseModel.FactChatConsultations` AS c
            ON uf.idUser = c.idUser
           AND uf.idCompany = c.idCompany
        LEFT JOIN `data-prd-424213.03_BaseModel.DimSpecialities` AS s
            ON c.idSpeciality = s.idSpeciality

        UNION ALL

        SELECT
            uf.GrupoContrato,
            COALESCE(
                s.SpecialityES,
                'Consulta sin especialidad'
            ) AS Especialidad
        FROM usuarios_filtrados AS uf
        JOIN `data-prd-424213.03_BaseModel.FactVideoCalls` AS v
            ON uf.idUser = v.idUser
           AND uf.idCompany = v.idCompany
        LEFT JOIN `data-prd-424213.03_BaseModel.DimSpecialities` AS s
            ON v.idSpeciality = s.idSpeciality
        WHERE v.VcStatus = 'finished'
    ) AS x
    GROUP BY x.GrupoContrato
),

grupos_contrato AS (
    SELECT DISTINCT
        GrupoContrato
    FROM usuarios_filtrados
)

SELECT
    gc.GrupoContrato AS UserContractNumber,
    COALESCE(ct.Chats, 0)
        + COALESCE(vt.Videollamadas, 0) AS `Numero de Consultas`,
    COALESCE(
        e.Especialidades,
        'Sin consultas'
    ) AS Especialidades,
    COALESCE(ct.Chats, 0) AS Chats,
    COALESCE(vt.Videollamadas, 0) AS Videollamadas
FROM grupos_contrato AS gc
LEFT JOIN chats_totales AS ct
    ON gc.GrupoContrato = ct.GrupoContrato
LEFT JOIN videollamadas_totales AS vt
    ON gc.GrupoContrato = vt.GrupoContrato
LEFT JOIN especialidades AS e
    ON gc.GrupoContrato = e.GrupoContrato
ORDER BY gc.GrupoContrato;