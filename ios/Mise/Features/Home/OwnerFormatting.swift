import Foundation

extension Money {
    var ownerDisplayValue: String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .currency
        formatter.currencyCode = currencyCode
        let digits = min(max(formatter.maximumFractionDigits, 0), 9)
        let divisor = NSDecimalNumber(
            mantissa: 1,
            exponent: Int16(digits),
            isNegative: false
        )
        let amount = NSDecimalNumber(value: minorUnits).dividing(by: divisor)
        return formatter.string(from: amount)
            ?? "\(currencyCode) \(minorUnits)"
    }
}

extension APIStringValue {
    var ownerDisplayName: String {
        rawValue
            .replacingOccurrences(of: "_", with: " ")
            .split(separator: " ")
            .map { $0.prefix(1).uppercased() + String($0.dropFirst()) }
            .joined(separator: " ")
    }
}
