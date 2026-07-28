SELECT DISTINCT
    u.UserToken                            AS token,
    u.UserFirstName                        AS userfirstname,
    u.UserLastName                         AS userlastname,
    s.SpecialityES                         AS speciality,
    v.idVc                                 AS videoconsultation_id,
    v.idRoom                               AS room_id,
    v.VcStatus                             AS status_vc,
    v.VcPetitionCreatedAtUTC               AS petition_created_at_utc,
    v.VcProfessionalAssignedAtUTC          AS professional_assigned_at_utc,
    v.VcProfessionalCallAtUTC              AS professional_call_at_utc,
    v.VcCallStartedAtUTC                   AS call_started_at_utc,
    v.VcCallFinishedAtUTC                  AS call_finished_at_utc,
    v.VcCancelledAtUTC                     AS cancelled_at_utc,
    v.VcEndedBy                            AS cancelled_by,
    v.VcDurationInSecs                     AS vc_duration_in_secs,
    v.VcTimeBetweenPetitionAndCancel       AS time_between_petition_and_cancel,
    v.VcWaitingTimeToCallInSecs            AS vc_waiting_time_to_call_in_secs,
    v.VcWaitingTimeToStartInSecs           AS vc_waiting_time_to_start_in_secs
FROM `data-prd-424213.03_BaseModel.FactVideoCalls` AS v
JOIN `data-prd-424213.03_BaseModel.DimUsers` AS u
    ON v.idUser = u.idUser
JOIN `data-prd-424213.03_BaseModel.DimProfessionals` AS p
    ON p.idProfessional = v.idProfessional
JOIN `data-prd-424213.03_BaseModel.DimSpecialities` AS s
    ON s.idSpeciality = p.idSpeciality
WHERE v.idCompany = @id_company
AND v.VcStatus = 'finished';
