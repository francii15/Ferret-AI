# Ferret
### A Windows snipping tool connected to an AI chatbox

Ferret brings screen capture and AI conversation into one desktop application. Users can capture a selected region of their screen, preview it in the chatbox, and ask questions about its contents. A floating ferret mascot provides quick access to the snipping tool and chat window.

## Project Overview

Screenshots often contain information that needs more than a simple capture: an error to investigate, a passage to explain, a question to solve, or text to extract. Ferret connects the captured image directly to a conversation, making it easier to work with that information without moving the screenshot between applications.

Snipping is the core function of the project. The AI chatbox extends that workflow by allowing users to add instructions and receive a response based on the screenshot and their question.

## Key Features

- **Screen region capture** — Select the area of the screen needed for a screenshot.
- **Integrated AI chatbox** — Preview a capture, add a question, and continue the conversation in the same window.
- **Floating ferret mascot** — Access Snip and Prompt actions from the desktop, with left, right, and top edge positions.
- **Image uploads** — Use existing images alongside newly captured screenshots.
- **Prompt shortcuts** — Choose actions such as Explain, Solve, Summarize, Extract, Debug, or Translate.
- **Conversation history** — Reopen previous conversations through Recents.
- **Image library** — Browse captured and uploaded images stored by the application.
- **Theme options** — Choose a dark, light, or contrast appearance.

## How It Works

The user selects **Snip**, drags across a screen region, and reviews the captured image in the chatbox. They can then type a question or choose a prompt shortcut before submitting the message. The AI response appears in the conversation, where follow-up questions can be added.

Capturing an image does not automatically start analysis. The user decides what to ask and when to send it. The chatbox also supports text conversations without an image.

## Example Uses

| Task | Use in Ferret |
| --- | --- |
| Understanding an error | Capture the error message and ask for an explanation or debugging guidance. |
| Reading on-screen content | Capture a passage and request a summary, explanation, or translation. |
| Working with image text | Submit a screenshot and request text extraction. |
| Studying a question | Capture the question and ask for a step-by-step explanation. |
| Discussing visual information | Attach an image and ask about its visible contents. |

## Built With

Ferret is developed in Python with PySide6 for the desktop interface and screen capture workflow. AI conversations connect through OpenRouter, with image analysis provided by compatible vision models. PyInstaller and Inno Setup support Windows application packaging and installation.

## AI Connection and Local Storage

AI responses require an internet connection and an OpenRouter API key. Screenshot analysis requires a model that supports images, and response availability depends on the selected provider.

Conversations, images, and preferences are stored locally. When a message is submitted, its included conversation context and attached images are sent to the AI service for processing.

## Author

Francis Infant
