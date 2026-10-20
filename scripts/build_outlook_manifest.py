"""Build a sideloadable Outlook manifest for a verified HTTPS add-in origin."""
from argparse import ArgumentParser
from pathlib import Path
from urllib.parse import urlsplit
from xml.sax.saxutils import escape


def manifest(origin: str) -> str:
    url = urlsplit(origin)
    if url.scheme != "https" or not url.netloc or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
        raise ValueError("Use an HTTPS origin with no path, query, credentials, or fragment.")
    base = escape(origin.rstrip("/"), {'"': '&quot;'})

    def overrides(version: str) -> str:
        pin = "<SupportsPinning>true</SupportsPinning>" if version == "1_1" else ""
        namespace = "http://schemas.microsoft.com/office/mailappversionoverrides" + ("/1.1" if version == "1_1" else "")
        child = overrides("1_1") if version == "1_0" else ""
        return f'''<VersionOverrides xmlns="{namespace}" xsi:type="VersionOverridesV{version}">
  <Requirements><bt:Sets DefaultMinVersion="1.9"><bt:Set Name="Mailbox"/></bt:Sets></Requirements>
  <Hosts><Host xsi:type="MailHost"><DesktopFormFactor>
    <FunctionFile resid="Commands.Url"/>
    <ExtensionPoint xsi:type="MessageReadCommandSurface"><OfficeTab id="TabDefault"><Group id="Rivet.Read.Group">
      <Label resid="Rivet.Group"/>
      <Control xsi:type="Button" id="Rivet.Generate">
        <Label resid="Rivet.Button"/>
        <Supertip><Title resid="Rivet.Button"/><Description resid="Rivet.Description"/></Supertip>
        <Icon><bt:Image size="16" resid="Icon.16"/><bt:Image size="32" resid="Icon.32"/><bt:Image size="80" resid="Icon.80"/></Icon>
        <Action xsi:type="ShowTaskpane"><SourceLocation resid="Taskpane.Url"/>{pin}</Action>
      </Control>
    </Group></OfficeTab></ExtensionPoint>
  </DesktopFormFactor></Host></Hosts>
  <Resources>
    <bt:Images><bt:Image id="Icon.16" DefaultValue="{base}/outlook/icon-16.png"/><bt:Image id="Icon.32" DefaultValue="{base}/outlook/icon-32.png"/><bt:Image id="Icon.80" DefaultValue="{base}/outlook/icon-80.png"/></bt:Images>
    <bt:Urls><bt:Url id="Commands.Url" DefaultValue="{base}/outlook/commands.html"/><bt:Url id="Taskpane.Url" DefaultValue="{base}/outlook/taskpane.html"/></bt:Urls>
    <bt:ShortStrings><bt:String id="Rivet.Group" DefaultValue="Rivet"/><bt:String id="Rivet.Button" DefaultValue="Reply with Rivet"/></bt:ShortStrings>
    <bt:LongStrings><bt:String id="Rivet.Description" DefaultValue="Prepare an evidence-linked reply for the selected order. Review it before opening a native Outlook reply."/></bt:LongStrings>
  </Resources>
  {child}
</VersionOverrides>'''

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<OfficeApp xmlns="http://schemas.microsoft.com/office/appforoffice/1.1" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:bt="http://schemas.microsoft.com/office/officeappbasictypes/1.0" xsi:type="MailApp">
  <Id>13ea7992-64a8-4971-b088-5c49d6d358b6</Id>
  <Version>1.0.0.0</Version>
  <ProviderName>Rivet</ProviderName>
  <DefaultLocale>en-US</DefaultLocale>
  <DisplayName DefaultValue="Rivet"/>
  <Description DefaultValue="Prepare responses from your order evidence, review them, and open a reply in the original Outlook thread."/>
  <IconUrl DefaultValue="{base}/outlook/icon-64.png"/>
  <HighResolutionIconUrl DefaultValue="{base}/outlook/icon-128.png"/>
  <SupportUrl DefaultValue="https://rivet-usa.com/pilot"/>
  <AppDomains><AppDomain>{base}</AppDomain></AppDomains>
  <Hosts><Host Name="Mailbox"/></Hosts>
  <Requirements><Sets><Set Name="Mailbox" MinVersion="1.9"/></Sets></Requirements>
  <FormSettings><Form xsi:type="ItemRead"><DesktopSettings><SourceLocation DefaultValue="{base}/outlook/taskpane.html"/><RequestedHeight>450</RequestedHeight></DesktopSettings></Form></FormSettings>
  <Permissions>ReadItem</Permissions>
  <Rule xsi:type="ItemIs" ItemType="Message" FormType="Read"/>
  <DisableEntityHighlighting>false</DisableEntityHighlighting>
  {overrides("1_0")}
</OfficeApp>
'''


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--origin", required=True)
    parser.add_argument("--output", type=Path, default=Path("apps/web/public/outlook/manifest.xml"))
    args = parser.parse_args()
    value = manifest(args.origin)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(value)
    print(f"Manifest written to {args.output}")
