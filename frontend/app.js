const sendButton = document.getElementById('send-btn');
const clearButton = document.getElementById("clear-btn")
const textArea = document.getElementById("text-area");
const agentRadio = document.getElementById("agent-btn")
const agentOutput = document.getElementById("agent-output")

function formatTime(iso) {
    return new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

const calendar = new FullCalendar.Calendar(document.getElementById("calendar"),
    {
        initialView: "dayGridMonth",
        eventClick: async (info) => {
            const ok = confirm("Delete " + info.event.title + "?")
            if (ok) {
                await fetch("/events/" + info.event.id, { method: "DELETE" })
                calendar.refetchEvents()
            }
        },
        events: fetchEvents
    }) 
calendar.render()

async function fetchEvents() {
    const response = await fetch('/events')
    const data = await response.json()

    const dataList = Object.entries(data)
    return dataList.map(([id, e]) => ({
        id: id,
        title: e.title,
        start: e.start_day_time,
        end: e.end_day_time
    }))
}

async function renderPending() {
    const pendingDiv = document.getElementById("pending");
    const response = await fetch('/pending');
    const data = await response.json();

    pendingDiv.replaceChildren();
    const pending = data.pending;
    if (pending === null) {
        pendingDiv.hidden = true;
        return;
    }
    pendingDiv.hidden = false;

    const info = document.createElement("span");
    const cancelButton = document.createElement("button");
    const confirmButton = document.createElement("button");

    cancelButton.textContent = "Cancel";
    confirmButton.textContent = "Confirm";

    const title = document.createElement("p"), time = document.createElement("p");
    title.textContent = pending.event.title;
    time.textContent = formatTime(pending.event.start_day_time);

    confirmButton.addEventListener("click", async () => {
        const res = await fetch("/pending/confirm", { method: "POST" });
        if (!res.ok) {
            const err = await res.json();
            alert(JSON.stringify(err.detail));
        }
        renderPending();
        calendar.refetchEvents();
    });

    cancelButton.addEventListener("click", async () => {
        await fetch("/pending", { method: "DELETE" });
        renderPending();
        calendar.refetchEvents();
    });

    info.appendChild(title);
    info.appendChild(time);
    pendingDiv.appendChild(info);
    pendingDiv.appendChild(confirmButton);
    pendingDiv.appendChild(cancelButton);
}

async function clickListener() {

    try {
        sendButton.disabled = true
        sendButton.textContent = "Thinking..."

        const text = textArea.value;
        let endpoint = agentRadio.checked ? "/agent" : "/command"
        agentOutput.replaceChildren()
        agentOutput.hidden = true

        const response = await fetch(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                request: text
            })
        });

        const data = await response.json()
        if (!response.ok) {
            alert(JSON.stringify(data.detail));
            return;
        }
        textArea.value = "";
        calendar.refetchEvents()
        renderPending();

        if (agentRadio.checked) renderAgentOutput(data)
    }
    catch (err) {
        alert("Something went wrong: " + err.message);
    }
    finally {
        sendButton.disabled = false
        sendButton.textContent = "Send"
    }
}

function renderAgentOutput(data) {
    agentOutput.hidden = false

    const reply = document.createElement("p")
    reply.textContent = "Reply: " + data.reply

    const list = document.createElement("ul")
    for (const action of data["actions"]) {
        
        const item = document.createElement("li")
        if (action.is_error === true) {
            item.textContent = "ERROR: " + action.tool + ": " + JSON.stringify(action.input)
        }
        else item.textContent = action.tool + ": " + JSON.stringify(action.input)

        list.appendChild(item)
    }
    agentOutput.appendChild(reply)
    agentOutput.appendChild(list)
}

const chips = document.getElementById("chips")

const modeNote = document.getElementById("mode-note")

const QUICK_PLACEHOLDER = textArea.placeholder
const QUICK_NOTE = "Single commands for interacting with a single event: add, remove, change."
const AGENT_NOTE = "More complex prompting capabilities for multiple events."
const AGENT_EXAMPLES = [
    "Add gym Monday, Wednesday, Friday 5-6pm repeating the next 2 weeks.",
    "Move 'placeholder' event to Friday, and add a dinner 7-8 the same night. Ensure I have 1 hour after dinner to rest.",
]

function renderChips() {
    for (const example of AGENT_EXAMPLES) {
        const chip = document.createElement("button")
        chip.type = "button"
        chip.className = "chip"
        chip.textContent = example
        chip.addEventListener("click", () => {
            textArea.value = example
            textArea.focus()
        })
        chips.appendChild(chip)
    }
}

function updateMode() {
    modeNote.textContent = agentRadio.checked ? AGENT_NOTE : QUICK_NOTE
    textArea.placeholder = agentRadio.checked ? "Describe what you want done" : QUICK_PLACEHOLDER
    chips.hidden = !agentRadio.checked
}

renderChips()
updateMode()
for (const radio of document.querySelectorAll('input[name="api-select"]')) {
    radio.addEventListener("change", updateMode)
}

async function clearEvents() {
    if (!confirm("Delete every event? This can't be undone.")) return

    try {
        clearButton.disabled = true
        const response = await fetch("/events", { method: "DELETE" })
        if (!response.ok) {
            const data = await response.json()
            alert(JSON.stringify(data.detail))
            return
        }
        agentOutput.replaceChildren()
        agentOutput.hidden = true
        calendar.refetchEvents()
        renderPending()
    }
    catch (err) {
        alert("Something went wrong: " + err.message)
    }
    finally {
        clearButton.disabled = false
    }
}

renderPending();
sendButton.addEventListener("click", clickListener);
clearButton.addEventListener("click", clearEvents)