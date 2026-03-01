You are a professional PineScript version=6 developer. You know how to code indicators and strategies and you also know their differences in code. I need your help to turn a TradingView indicator into a strategy please.

Respect these instructions:

* Convert all Indicator specific code to Strategy specific code. Don't use any code that a TradingView Strategy won't support. Especially timeframes and gaps. Define those in code so they are semantically the same as before.
* Preserve the timeframe logic if there is one. Fill gaps.
* If the indicator is plotting something, the strategy code shall plot the same thing as well so the visuals are preserved.
* Don't trigger a short. Simply go Long and Flat.
* Always use 100% of capital.
* Set commission to 0.1%.
* Set slippage to 3.
* strategy.commission.percent and strategy.slippage don't exist in PineScript. Please avoid this mistake. Set those variables in the strategy() function when initiating the strategy.
* When initiating the strategy() function, don't use line breaks as this will cause a compiler error.
* Leave all other strategy settings to default values (aka. don't set them at all).
* Never use lookahead_on because that’s cheating.
* Add Start Date and End Date inputs/filters so the user can choose from when to when to execute trades. Start with 1st January 2018 and go to 31st December 2069.
* When setting the title of the strategy, add "copilot - " at the start of the name and then continue with the name of the strategy.