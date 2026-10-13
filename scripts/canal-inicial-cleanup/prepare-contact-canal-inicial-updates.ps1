param(
    [Parameter(Mandatory = $true)]
    [string]$OpportunityCsv,

    [Parameter(Mandatory = $true)]
    [string]$ContactsCurrentCsv,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null

$desiredAllPath = Join-Path $OutputDirectory 'contact_canal_inicial_all.csv'
$updatesQuotedPath = Join-Path $OutputDirectory 'contact_canal_inicial_updates.csv'
$updatesPlainPath = Join-Path $OutputDirectory 'contact_canal_inicial_updates_plain.csv'
$backupPath = Join-Path $OutputDirectory 'contact_canal_inicial_backup_before_update.csv'

$opportunityRows = Import-Csv $OpportunityCsv
$firstCanalByContact = [ordered]@{}

foreach ($row in $opportunityRows) {
    $contactId = $row.npsp__Primary_Contact__c
    if ($contactId -and -not $firstCanalByContact.Contains($contactId)) {
        $firstCanalByContact[$contactId] = $row.Canal__c
    }
}

$desiredRows = foreach ($entry in $firstCanalByContact.GetEnumerator()) {
    [pscustomobject]@{
        Id               = $entry.Key
        Canal_Inicial__c = $entry.Value
    }
}

$desiredRows | Export-Csv $desiredAllPath -NoTypeInformation -Encoding UTF8

$desiredById = @{}
foreach ($row in $desiredRows) {
    $desiredById[$row.Id] = $row.Canal_Inicial__c
}

$updates = New-Object System.Collections.Generic.List[object]
$backup = New-Object System.Collections.Generic.List[object]

foreach ($contact in Import-Csv $ContactsCurrentCsv) {
    if ($desiredById.ContainsKey($contact.Id)) {
        $newValue = $desiredById[$contact.Id]

        if ($contact.Canal_Inicial__c -ne $newValue) {
            $updates.Add([pscustomobject]@{
                Id               = $contact.Id
                Canal_Inicial__c = $newValue
            })

            $backup.Add([pscustomobject]@{
                Id               = $contact.Id
                Canal_Inicial__c = $contact.Canal_Inicial__c
            })
        }

        $desiredById.Remove($contact.Id)
    }
}

$updates | Export-Csv $updatesQuotedPath -NoTypeInformation -Encoding UTF8
$backup | Export-Csv $backupPath -NoTypeInformation -Encoding UTF8

$plainLines = New-Object System.Collections.Generic.List[string]
$plainLines.Add('Id,Canal_Inicial__c')
foreach ($row in $updates) {
    $plainLines.Add(($row.Id + ',' + $row.Canal_Inicial__c))
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($updatesPlainPath, $plainLines, $utf8NoBom)

[pscustomobject]@{
    OpportunityRows       = $opportunityRows.Count
    UniqueContacts        = $firstCanalByContact.Count
    UpdatesNeeded         = $updates.Count
    MissingContacts       = $desiredById.Count
    DesiredAllCsv         = $desiredAllPath
    UpdatesCsv            = $updatesQuotedPath
    UpdatesPlainCsv       = $updatesPlainPath
    BackupBeforeUpdateCsv = $backupPath
}
