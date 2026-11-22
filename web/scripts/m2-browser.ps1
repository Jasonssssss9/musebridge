param(
    [Parameter(Mandatory=$true)][string]$BaseUrl,
    [Parameter(Mandatory=$true)][string]$OutputDirectory
)

# Run the existing Windows Chrome from WSL without installing browser packages.
# This is an automated development check, not independent visitor sign-off.
$ErrorActionPreference = 'Stop'
$endpoint = [Uri]$BaseUrl
if ($endpoint.Scheme -ne 'http' -or $endpoint.Host -ne '127.0.0.1') { throw 'Use the controlled loopback frontend origin.' }
if (!(Test-Path -LiteralPath $OutputDirectory)) { throw 'Create the private output directory first.' }
$reportPath = Join-Path $OutputDirectory 'browser-report.json'
if (Test-Path -LiteralPath $reportPath) { throw 'Browser report already exists; use a new output directory.' }
$browserExecutable = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
if (!(Test-Path -LiteralPath $browserExecutable)) { $browserExecutable = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe' }
if (!(Test-Path -LiteralPath $browserExecutable)) { throw 'No existing Windows Chrome or Edge was found.' }
$profileDirectory = Join-Path $env:TEMP ('musebridge-m2-' + [Guid]::NewGuid().ToString('N'))
$listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
$listener.Start()
$debugPort = $listener.LocalEndpoint.Port
$listener.Stop()
$script:cdpSocket = $null
$script:cdpSequence = 0
$browserProcess = $null
$results = [Collections.Generic.List[object]]::new()
$report = @{ mode='controlled-real-browser-development'; independent_acceptance=$false; cases=$results }

function Invoke-Cdp([string]$Method, [hashtable]$Parameters=@{}) {
    $script:cdpSequence++
    $requestId = $script:cdpSequence
    $wire = @{ id=$requestId; method=$Method; params=$Parameters } | ConvertTo-Json -Depth 60 -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($wire)
    $segment = [ArraySegment[byte]]::new($bytes)
    $cancellation = [Threading.CancellationTokenSource]::new(90000)
    try {
        $null=$script:cdpSocket.SendAsync($segment, [Net.WebSockets.WebSocketMessageType]::Text, $true, $cancellation.Token).GetAwaiter().GetResult()
        while ($true) {
            $message = [IO.MemoryStream]::new()
            do {
                $buffer = New-Object byte[] 65536
                $received = $script:cdpSocket.ReceiveAsync([ArraySegment[byte]]::new($buffer), $cancellation.Token).GetAwaiter().GetResult()
                if ($received.MessageType -eq [Net.WebSockets.WebSocketMessageType]::Close) { throw 'Browser debugging connection closed.' }
                $message.Write($buffer,0,$received.Count)
                if ($message.Length -gt 8388608) { throw 'Browser debugging response exceeded its bound.' }
            } while (!$received.EndOfMessage)
            $value = [Text.Encoding]::UTF8.GetString($message.ToArray()) | ConvertFrom-Json
            $message.Dispose()
            if ($value.id -eq $requestId) {
                if ($value.error) { throw ('Browser command failed: '+$Method) }
                return $value.result
            }
        }
    } finally { $cancellation.Dispose() }
}

function Invoke-Evaluate([string]$Expression) {
    $reply = Invoke-Cdp 'Runtime.evaluate' @{ expression=$Expression; awaitPromise=$true; returnByValue=$true }
    if ($reply.exceptionDetails) { throw 'Browser page evaluation failed.' }
    return $reply.result.value
}

function Wait-Javascript([string]$Expression, [int]$Seconds=80) {
    $until = [DateTime]::UtcNow.AddSeconds($Seconds)
    while ([DateTime]::UtcNow -lt $until) {
        try { if (Invoke-Evaluate $Expression) { return } } catch { }
        Start-Sleep -Milliseconds 150
    }
    throw 'Browser page did not reach the expected state in time.'
}

function Read-Page {
    return Invoke-Evaluate @'
(() => {
 const calls=window.__m2Calls||[];
 const turn=document.querySelector('ol.turns > li:last-child');
 const answer=turn?.querySelector('.answer-text');
 const last=(pred)=>calls.slice().reverse().find(pred)||null;
 return {url:location.href,text:document.body.innerText,
  conversation:last(c=>c.body&&c.body.conversation)?.body.conversation||null,
  last_post:last(c=>c.method==='POST'&&c.url.endsWith('/messages')),
  last_history:last(c=>c.method==='GET'&&c.url.endsWith('/messages')),
  observed_waiting:window.__m2ObservedWaiting,
  response_hidden:window.__m2ResponseHidden||false,
  turns:document.querySelectorAll('ol.turns > li').length,
  sources:document.querySelectorAll('a.cite-link').length,
  last_answer:answer?.textContent||null,last_answer_language:answer?.lang||null,
  last_links:Array.from(turn?.querySelectorAll('a.cite-link')||[],link=>link.href),
  last_quotes:Array.from(turn?.querySelectorAll('blockquote')||[],quote=>quote.textContent),
  quotes:document.querySelectorAll('blockquote').length,
  language:document.querySelector('select')?.value||null};
})()
'@
}

function Test-MaterialTurn($State, [string]$Artwork, [string]$Language) {
    $turn=$State.last_post.body.turn
    $citations=@($turn.answer.citations)
    if ($State.last_post.status -ne 200 -or $turn.answer.status -ne 'answered' -or $citations.Count -ne 1) { return $false }
    $citation=$citations[0]
    # This test asks only for the released medium. Verify both translated
    # concepts in the rendered answer to catch a mislabeled English response.
    $mediumTerms=@{
        'en'=@('\boil\b','\bcanvas\b')
        'es'=@('óleo|aceite','lienzo|tela')
        'zh-CN'=@('油画|油彩|油性|油料','布')
        'ko'=@('유채|유화|유성','캔버스|화포|천')
    }
    $rubric=$mediumTerms[$Language]
    $wording=($State.last_answer -match $rubric[0] -and $State.last_answer -match $rubric[1])
    return ($wording -and $turn.answer_language -eq $Language -and $State.last_answer_language -eq $Language -and
        $State.last_answer -ceq $turn.answer.text -and $State.conversation.artwork_id -eq $Artwork -and
        $citation.chunk_id -eq ($Artwork+'-medium') -and $citation.excerpt -ceq 'Oil on canvas' -and
        $State.last_quotes -ccontains $citation.excerpt -and $State.last_links -ccontains $citation.source_url)
}

function Open-Artwork([string]$Artwork) {
    $null=Invoke-Cdp 'Page.navigate' @{url=($BaseUrl+'/a/'+$Artwork)}
    Wait-Javascript "Boolean(document.querySelector('textarea') && (window.__m2Calls||[]).some(c=>c.body&&c.body.conversation))" 30
    return Read-Page
}

function Submit-Question([string]$Question,[string]$Language) {
    # After a failed case, Edit is an explicit new visitor message, not a retry.
    $null=Invoke-Evaluate "(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Edit question');if(b)b.click();})()"
    if ($Language) {
        $selection=$Language|ConvertTo-Json -Compress
        $null=Invoke-Evaluate "(()=>{const s=document.querySelector('select');s.value=$selection;s.dispatchEvent(new Event('change',{bubbles:true}));})()"
    }
    $before=Invoke-Evaluate "(window.__m2Calls||[]).filter(c=>c.method==='POST'&&c.url.endsWith('/messages')).length"
    $null=Invoke-Evaluate "document.querySelector('textarea').focus()"
    $null=Invoke-Cdp 'Input.dispatchKeyEvent' @{type='keyDown';key='a';code='KeyA';windowsVirtualKeyCode=65;modifiers=2}
    $null=Invoke-Cdp 'Input.dispatchKeyEvent' @{type='keyUp';key='a';code='KeyA';windowsVirtualKeyCode=65;modifiers=2}
    $null=Invoke-Cdp 'Input.insertText' @{text=$Question}
    Wait-Javascript "Boolean(document.querySelector('form button[type=submit]')&&!document.querySelector('form button[type=submit]').disabled)" 10
    $null=Invoke-Evaluate "document.querySelector('form').requestSubmit()"
    Wait-Javascript "(window.__m2Calls||[]).filter(c=>c.method==='POST'&&c.url.endsWith('/messages')).length>$before" 80
    Start-Sleep -Milliseconds 300
    return Read-Page
}

try {
    $launchArguments=@('--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
        '--disable-background-networking','--disable-component-update','--disable-sync',
        ('--remote-debugging-port='+$debugPort),('--user-data-dir="'+$profileDirectory+'"'),'about:blank')
    $browserProcess=Start-Process -FilePath $browserExecutable -ArgumentList $launchArguments -PassThru -WindowStyle Hidden
    $until=[DateTime]::UtcNow.AddSeconds(20)
    do {
        try { $version=Invoke-RestMethod ('http://127.0.0.1:'+ $debugPort +'/json/version'); break } catch { Start-Sleep -Milliseconds 200 }
    } while ([DateTime]::UtcNow -lt $until)
    if (!$version) { throw 'Existing Windows browser did not open its private debugging endpoint.' }
    $report.browser=$version.Browser
    $pages=Invoke-RestMethod ('http://127.0.0.1:'+ $debugPort +'/json/list')
    $page=@($pages|Where-Object {$_.type -eq 'page'})[0]
    $script:cdpSocket=[Net.WebSockets.ClientWebSocket]::new()
    $null=$script:cdpSocket.ConnectAsync([Uri]$page.webSocketDebuggerUrl,[Threading.CancellationToken]::None).GetAwaiter().GetResult()
    $null=Invoke-Cdp 'Page.enable'
    $null=Invoke-Cdp 'Runtime.enable'
    $null=Invoke-Cdp 'Emulation.setDeviceMetricsOverride' @{width=390;height=844;deviceScaleFactor=1;mobile=$true}
    $instrument=@'
window.__m2Calls=[];
window.__m2ObservedWaiting=false;
new MutationObserver(()=>{if(document.querySelector('.waiting[role=status]'))window.__m2ObservedWaiting=true;})
  .observe(document,{childList:true,subtree:true});
const m2Fetch=window.fetch.bind(window);
window.fetch=async(...args)=>{const response=await m2Fetch(...args);
 response.clone().json().then(body=>window.__m2Calls.push({url:String(args[0]),method:args[1]?.method||'GET',status:response.status,body})).catch(()=>{});
 if(window.__m2HideNextPost && response.ok && args[1]?.method==='POST' && String(args[0]).endsWith('/messages')){
   window.__m2HideNextPost=false; window.__m2ResponseHidden=true;
   throw new TypeError('Simulated loss of the committed HTTP response');
 }
 return response;};
'@
    $null=Invoke-Cdp 'Page.addScriptToEvaluateOnNewDocument' @{source=$instrument}
    $first=Open-Artwork 'aic-28560'
    $firstId=$first.conversation.conversation_id
    $material=Submit-Question 'What material is this painting made of?' 'en'
    $results.Add(@{name='bedroom-material';state=$material;pass=(Test-MaterialTurn $material 'aic-28560' 'en');http_pass=($material.last_post.status -eq 200);source_visible=($material.sources -gt 0)})
    $firstTurn=$material.last_post.body.turn.turn_id
    $null=Invoke-Cdp 'Page.reload' @{ignoreCache=$true}
    Wait-Javascript "Boolean(document.querySelector('textarea')&&(window.__m2Calls||[]).some(c=>c.method==='GET'&&c.url.endsWith('/messages')))" 30
    $refreshed=Read-Page
    $results.Add(@{name='refresh';pass=($refreshed.conversation.conversation_id -eq $firstId -and @($refreshed.last_history.body.turns|Where-Object {$_.turn_id -eq $firstTurn}).Count -eq 1);state=$refreshed})
    $followup=Submit-Question 'Could you remind me of that material?' ''
    $results.Add(@{name='material-followup';pass=(Test-MaterialTurn $followup 'aic-28560' 'en');http_pass=($followup.last_post.status -eq 200);state=$followup})
    $second=Open-Artwork 'aic-11723'
    $results.Add(@{name='artwork-isolation';pass=($second.conversation.conversation_id -ne $firstId -and $second.conversation.revision -eq 0);state=$second})
    foreach ($item in @(
        @{language='zh-CN';text='这幅画是什么材质？'},
        @{language='es';text='¿De qué material está hecha esta pintura?'},
        @{language='ko';text='이 그림은 어떤 재료로 그렸나요?'})) {
        $state=Submit-Question $item.text $item.language
        $results.Add(@{name=('material-'+$item.language);pass=(Test-MaterialTurn $state 'aic-11723' $item.language);http_pass=($state.last_post.status -eq 200);state=$state})
    }
    $screenshot=Invoke-Cdp 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}
    [IO.File]::WriteAllBytes((Join-Path $OutputDirectory 'browser.png'),[Convert]::FromBase64String($screenshot.data))
    $returned=Open-Artwork 'aic-28560'
    $results.Add(@{name='switch-back';pass=($returned.conversation.conversation_id -eq $firstId -and $returned.conversation.revision -ge 1);state=$returned})
    # Drop exactly one successful response after the real server has committed it.
    $null=Invoke-Evaluate 'window.__m2ObservedWaiting=false; window.__m2HideNextPost=true;'
    $recoveryQuestion='Thank you for helping me with my notes.'
    $interrupted=Submit-Question $recoveryQuestion 'en'
    if ($interrupted.response_hidden) {
        Wait-Javascript "Boolean((window.__m2Calls||[]).some(c=>c.method==='GET' && c.body?.turns?.some(t=>t.question==='$recoveryQuestion'))&&!document.querySelector('.waiting'))" 30
    }
    $recovered=Read-Page
    $saved=@($recovered.last_history.body.turns|Where-Object {$_.question -eq $recoveryQuestion})
    $stable=($recovered.response_hidden -and $saved.Count -eq 1 -and $recovered.last_post.body.turn.turn_id -eq $saved[0].turn_id -and
        $recovered.conversation.revision -eq ($returned.conversation.revision + 1) -and
        $recovered.last_answer -ceq $saved[0].answer.text -and $recovered.last_answer_language -eq 'en')
    $results.Add(@{name='waiting';pass=($recovered.observed_waiting -eq $true)})
    $results.Add(@{name='recover-committed-response';pass=$stable;state=$recovered})
    Write-Output ('Browser cases recorded: '+$results.Count)
} catch {
    $report.fatal_error=$_.Exception.Message
    Write-Output 'Browser check stopped; inspect its private report.'
} finally {
    [IO.File]::WriteAllText($reportPath,($report|ConvertTo-Json -Depth 60),[Text.UTF8Encoding]::new($false))
    if ($script:cdpSocket) { try {$null=Invoke-Cdp 'Browser.close'} catch {}; $script:cdpSocket.Dispose() }
    if ($browserProcess -and !$browserProcess.HasExited) {
        if (!$browserProcess.WaitForExit(5000)) { $null=& taskkill.exe /PID $browserProcess.Id /T /F 2>&1 }
    }
    if (Test-Path -LiteralPath $profileDirectory) {
        $actual=(Resolve-Path -LiteralPath $profileDirectory).ProviderPath
        $tempRoot=(Resolve-Path -LiteralPath $env:TEMP).ProviderPath
        if ((Split-Path -Parent $actual) -ne $tempRoot -or (Split-Path -Leaf $actual) -notmatch '^musebridge-m2-[a-f0-9]{32}$') {
            throw 'Unexpected private browser profile path.'
        }
        Remove-Item -LiteralPath $actual -Recurse -Force
    }
}
if ($report.fatal_error -or @($results | Where-Object { $_.pass -ne $true }).Count -gt 0) { exit 1 }
