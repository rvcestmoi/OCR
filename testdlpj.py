import os
import win32com.client

DOWNLOAD_FOLDER = r"C:\git\OCR\OCR\PJ"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
inbox = outlook.GetDefaultFolder(6)

messages = inbox.Items
messages.Sort("[ReceivedTime]", True)

count = 0

for message in messages:

    message_id = message.EntryID
    subject = message.Subject
    sender = message.SenderEmailAddress

    if message.Attachments.Count > 0:

        for i in range(1, message.Attachments.Count + 1):

            attachment = message.Attachments.Item(i)
            filename = attachment.FileName

            if filename.lower().endswith(".pdf"):

                filepath = os.path.join(DOWNLOAD_FOLDER, filename)
                attachment.SaveAsFile(filepath)

                print("✅ PDF téléchargé :", filename)
                print("   📩 Sujet :", subject)
                print("   👤 Expéditeur :", sender)
                print("   🆔 Message ID :", message_id)
                print("-" * 50)

                count += 1

                if count >= 5:
                    print("\n🎯 Terminé : 5 PDF récupérés.")
                    exit()
