param(
    [Parameter(Mandatory = $true)]
    [string] $InputCsv,

    [Parameter(Mandatory = $true)]
    [string] $ReviewCsv,

    [Parameter(Mandatory = $true)]
    [string] $UpdateCsv,

    [Parameter(Mandatory = $true)]
    [string] $AppliedChangesCsv
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-DigitsOnly {
    param([string] $Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return ''
    }

    return ([regex]::Replace($Value, '[^0-9]', ''))
}

function Normalize-MexicanPhone {
    param([AllowNull()][string] $OriginalValue)

    $result = [ordered]@{
        Original = $OriginalValue
        Cleaned = ''
        Normalized = ''
        Status = ''
        RejectionReason = ''
        IsValid = $false
    }

    if ([string]::IsNullOrWhiteSpace($OriginalValue)) {
        $result.Status = 'INVALID_EMPTY'
        $result.RejectionReason = 'Value is blank or null.'
        return [pscustomobject]$result
    }

    $raw = $OriginalValue.Trim()

    if ($raw -match '[\t\r\n]') {
        $result.Status = 'INVALID_HAS_EXTENSION_OR_TEXT'
        $result.RejectionReason = 'Value contains an embedded tab or line break.'
        return [pscustomobject]$result
    }

    if ($raw -match '(?i)\b(ext|extension|extension|ext\.|x)\b') {
        $result.Status = 'INVALID_HAS_EXTENSION_OR_TEXT'
        $result.RejectionReason = 'Value contains an extension marker or additional text.'
        return [pscustomobject]$result
    }

    $phoneLikeParts = @($raw -split '[/;|]' | Where-Object { (Get-DigitsOnly $_).Length -ge 7 })
    if ($raw -match '[/;|]' -and $phoneLikeParts.Count -gt 1) {
        $result.Status = 'INVALID_MULTIPLE_NUMBERS'
        $result.RejectionReason = 'Value appears to contain multiple phone numbers.'
        return [pscustomobject]$result
    }

    $cleaned = $raw
    foreach ($token in @(' ', '-', '(', ')', '.', '/', "`t", "`r", "`n")) {
        $cleaned = $cleaned.Replace($token, '')
    }
    $result.Cleaned = $cleaned

    $plusCount = ([regex]::Matches($cleaned, '\+')).Count
    if ($plusCount -gt 1 -or ($plusCount -eq 1 -and -not $cleaned.StartsWith('+'))) {
        $result.Status = 'INVALID_BAD_PLUS_SIGN'
        $result.RejectionReason = 'Value contains multiple plus signs or a plus sign outside the first position.'
        return [pscustomobject]$result
    }

    if ($cleaned.StartsWith('+')) {
        if ($cleaned -match '^\+5252[0-9]{10}$') {
            $result.Normalized = '+52' + $cleaned.Substring(5)
            $result.Status = 'VALID_NORMALIZED'
            $result.IsValid = $true
            return [pscustomobject]$result
        }

        if ($cleaned -match '^\+52[0-9]{10}$') {
            $result.Normalized = $cleaned
            $result.Status = 'VALID_ALREADY_NORMALIZED'
            $result.IsValid = $true
            return [pscustomobject]$result
        }

        if (-not $cleaned.StartsWith('+52')) {
            $result.Status = 'INVALID_FOREIGN_NUMBER'
            $result.RejectionReason = 'International number does not start with +52.'
            return [pscustomobject]$result
        }

        if ($cleaned.Length -lt 13) {
            $result.Status = 'INVALID_TOO_SHORT'
            $result.RejectionReason = 'Mexican international number is shorter than +52 plus 10 digits.'
            return [pscustomobject]$result
        }

        if ($cleaned.Length -gt 13) {
            $result.Status = 'INVALID_TOO_LONG'
            $result.RejectionReason = 'Mexican international number is longer than +52 plus 10 digits.'
            return [pscustomobject]$result
        }

        $result.Status = 'INVALID_13_NOT_PLUS_52'
        $result.RejectionReason = '13-character value is not in +52 plus 10 digits format.'
        return [pscustomobject]$result
    }

    if ($cleaned -match '[^0-9]') {
        $result.Status = 'INVALID_HAS_EXTENSION_OR_TEXT'
        $result.RejectionReason = 'Value contains letters, symbols, or extra text after cleanup.'
        return [pscustomobject]$result
    }

    if ($cleaned -match '^[0-9]{10}$') {
        $result.Normalized = '+52' + $cleaned
        $result.Status = 'VALID_NORMALIZED'
        $result.IsValid = $true
        return [pscustomobject]$result
    }

    if ($cleaned -match '^52[0-9]{10}$') {
        $result.Normalized = '+' + $cleaned
        $result.Status = 'VALID_NORMALIZED'
        $result.IsValid = $true
        return [pscustomobject]$result
    }

    if ($cleaned.Length -lt 10) {
        $result.Status = 'INVALID_TOO_SHORT'
        $result.RejectionReason = 'Value has fewer than 10 digits.'
        return [pscustomobject]$result
    }

    if ($cleaned.Length -eq 11) {
        $result.Status = 'INVALID_11_DIGITS'
        $result.RejectionReason = 'Value has 11 digits.'
        return [pscustomobject]$result
    }

    if ($cleaned.Length -eq 12 -and -not $cleaned.StartsWith('52')) {
        $result.Status = 'INVALID_12_NOT_52'
        $result.RejectionReason = '12-digit value does not start with country code 52.'
        return [pscustomobject]$result
    }

    if ($cleaned.Length -eq 13) {
        $result.Status = 'INVALID_13_NOT_PLUS_52'
        $result.RejectionReason = '13-character value is not in +52 plus 10 digits format.'
        return [pscustomobject]$result
    }

    if ($cleaned.Length -gt 13) {
        $result.Status = 'INVALID_TOO_LONG'
        $result.RejectionReason = 'Value is longer than the supported Mexican phone formats.'
        return [pscustomobject]$result
    }

    $result.Status = 'INVALID_UNKNOWN'
    $result.RejectionReason = 'Value did not match any accepted or specific rejection rule.'
    return [pscustomobject]$result
}

$contacts = Import-Csv -Path $InputCsv
$reviewRows = New-Object System.Collections.Generic.List[object]
$updateRows = New-Object System.Collections.Generic.List[object]
$appliedRows = New-Object System.Collections.Generic.List[object]

foreach ($contact in $contacts) {
    $rowUpdate = [ordered]@{ Id = $contact.Id }
    $hasUpdate = $false

    foreach ($fieldName in @('HomePhone', 'MobilePhone')) {
        $original = $contact.$fieldName
        $evaluation = Normalize-MexicanPhone -OriginalValue $original
        $wouldUpdate = $evaluation.IsValid -and $evaluation.Normalized -ne $original

        $reviewRows.Add([pscustomobject][ordered]@{
            ContactId = $contact.Id
            ContactName = $contact.Name
            Field = $fieldName
            OriginalValue = $original
            CleanedValue = $evaluation.Cleaned
            ProposedNormalizedValue = $evaluation.Normalized
            Status = $evaluation.Status
            RejectionReason = $evaluation.RejectionReason
            WouldUpdateSalesforce = $(if ($wouldUpdate) { 'Yes' } else { 'No' })
        })

        if ($wouldUpdate) {
            $rowUpdate[$fieldName] = $evaluation.Normalized
            $hasUpdate = $true
            $appliedRows.Add([pscustomobject][ordered]@{
                ContactId = $contact.Id
                ContactName = $contact.Name
                Field = $fieldName
                PreviousValue = $original
                NewValue = $evaluation.Normalized
                Status = $evaluation.Status
            })
        }
    }

    if ($hasUpdate) {
        $updateRows.Add([pscustomobject]$rowUpdate)
    }
}

$reviewRows | Export-Csv -Path $ReviewCsv -NoTypeInformation -Encoding UTF8
$updateRows | Export-Csv -Path $UpdateCsv -NoTypeInformation -Encoding UTF8
$appliedRows | Export-Csv -Path $AppliedChangesCsv -NoTypeInformation -Encoding UTF8

[pscustomobject]@{
    ContactsEvaluated = $contacts.Count
    PhoneValuesEvaluated = $reviewRows.Count
    ContactsToUpdate = $updateRows.Count
    FieldChangesToApply = $appliedRows.Count
    ReviewCsv = $ReviewCsv
    UpdateCsv = $UpdateCsv
    AppliedChangesCsv = $AppliedChangesCsv
}
