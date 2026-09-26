# Browser specs run headless Chrome over CDP (Cuprite): no chromedriver.
# BROWSER_PATH points at Chrome/Chromium when it isn't on the PATH.
require "capybara/cuprite"

Capybara.register_driver(:hub_cuprite) do |app|
  Capybara::Cuprite::Driver.new(app, window_size: [ 1400, 1000 ], headless: true, process_timeout: 30, timeout: 15,
                                     browser_path: ENV["BROWSER_PATH"].presence, browser_options: { "no-sandbox" => nil })
end
Capybara.default_max_wait_time = 5

RSpec.configure do |config|
  config.include Devise::Test::IntegrationHelpers, type: :system
  config.before(:each, type: :system) { driven_by :hub_cuprite }
end
