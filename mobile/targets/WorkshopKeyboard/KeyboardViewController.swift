import UIKit

/// The Workshop keyboard: the clipboard's recent clips, newest first; a tap
/// types the clip into the field. Listing them needs Full Access (network and
/// the app group) and the sign-in the Workshop app leaves in the group.
final class KeyboardViewController: UIInputViewController, UITableViewDataSource, UITableViewDelegate {
    private let list = UITableView(frame: .zero, style: .plain)
    private let message = UILabel()
    private let globe = UIButton(type: .system)
    private var clips: [Clip] = []
    private var loading: Task<Void, Never>?

    override func viewDidLoad() {
        super.viewDidLoad()
        let height = view.heightAnchor.constraint(equalToConstant: 260)
        height.priority = UILayoutPriority(999)
        height.isActive = true

        list.dataSource = self
        list.delegate = self
        list.backgroundColor = .clear
        list.rowHeight = UITableView.automaticDimension
        list.estimatedRowHeight = 52
        list.accessibilityIdentifier = "workshop.keyboard.clips"

        message.numberOfLines = 0
        message.textAlignment = .center
        message.textColor = .secondaryLabel
        message.font = .preferredFont(forTextStyle: .subheadline)
        message.accessibilityIdentifier = "workshop.keyboard.message"

        globe.setImage(UIImage(systemName: "globe"), for: .normal)
        globe.tintColor = .label
        globe.accessibilityLabel = "Next keyboard"
        globe.addTarget(self, action: #selector(handleInputModeList(from:with:)), for: .allTouchEvents)

        for sub in [list, message, globe] as [UIView] {
            sub.translatesAutoresizingMaskIntoConstraints = false
            view.addSubview(sub)
        }
        NSLayoutConstraint.activate([
            list.topAnchor.constraint(equalTo: view.topAnchor),
            list.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            list.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            list.bottomAnchor.constraint(equalTo: globe.topAnchor),
            message.centerYAnchor.constraint(equalTo: list.centerYAnchor),
            message.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 24),
            message.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -24),
            globe.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 8),
            globe.bottomAnchor.constraint(equalTo: view.bottomAnchor, constant: -4),
            globe.widthAnchor.constraint(equalToConstant: 44),
            globe.heightAnchor.constraint(equalToConstant: 40),
        ])
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        reload()
    }

    override func viewWillLayoutSubviews() {
        globe.isHidden = !needsInputModeSwitchKey
        super.viewWillLayoutSubviews()
    }

    private func reload() {
        loading?.cancel()
        guard hasFullAccess else {
            show([], note: "Turn on Allow Full Access for this keyboard to see your clips: "
                + "Settings > General > Keyboard > Keyboards.")
            return
        }
        if clips.isEmpty { show([], note: "Loading clips") }
        loading = Task { [weak self] in
            do {
                let found = try await ClipsSource.recent()
                guard !Task.isCancelled else { return }
                self?.show(found, note: found.isEmpty ? "No clips yet." : nil)
            } catch {
                guard !Task.isCancelled else { return }
                let note = (error as? ClipsError)?.message ?? error.localizedDescription
                self?.show([], note: note)
            }
        }
    }

    private func show(_ found: [Clip], note: String?) {
        clips = found
        list.reloadData()
        message.text = note
        message.isHidden = note == nil
    }

    func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int {
        clips.count
    }

    func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let cell = tableView.dequeueReusableCell(withIdentifier: "clip")
            ?? UITableViewCell(style: .subtitle, reuseIdentifier: "clip")
        let clip = clips[indexPath.row]
        let line = clip.text.split(whereSeparator: \.isNewline).joined(separator: " ")
        cell.backgroundColor = .clear
        cell.textLabel?.text = line
        cell.textLabel?.numberOfLines = 2
        cell.detailTextLabel?.text = clip.source_label
        cell.detailTextLabel?.textColor = .secondaryLabel
        cell.accessibilityLabel = line
        return cell
    }

    func tableView(_ tableView: UITableView, didSelectRowAt indexPath: IndexPath) {
        tableView.deselectRow(at: indexPath, animated: true)
        textDocumentProxy.insertText(clips[indexPath.row].text)
    }
}
