FactoryBot.define do
  factory :user do
    sequence(:email) { |n| "user#{n}@example.com" }
    name { "Test User" }
    password { "password123" }
    password_confirmation { "password123" }
    role { :member }
    notify_email { true }
    notify_discord { false }

    trait :admin do
      role { :admin }
    end
  end
end
